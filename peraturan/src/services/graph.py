"""Neo4j graph database service for legal relationships."""

from typing import Optional, Any, TYPE_CHECKING
from contextlib import contextmanager

from src.config import Config, config as default_config
from src.models.peraturan import Peraturan
from src.utils.logging import get_logger

logger = get_logger("graph")

# Lazy import for neo4j - only load when actually needed
_neo4j_available = None
_GraphDatabase = None
_ServiceUnavailable = None
_AuthError = None


def _ensure_neo4j():
    """Lazy load neo4j module. Returns True if available."""
    global _neo4j_available, _GraphDatabase, _ServiceUnavailable, _AuthError

    if _neo4j_available is not None:
        return _neo4j_available

    try:
        from neo4j import GraphDatabase, Driver, Session
        from neo4j.exceptions import ServiceUnavailable, AuthError
        _GraphDatabase = GraphDatabase
        _ServiceUnavailable = ServiceUnavailable
        _AuthError = AuthError
        _neo4j_available = True
        return True
    except ImportError:
        logger.warning("neo4j package not installed. Graph features disabled.")
        _neo4j_available = False
        return False


# Status mapping from Indonesian to normalized
STATUS_MAP = {
    "Berlaku": "BERLAKU",
    "Tidak Berlaku": "TIDAK_BERLAKU",
    "Dicabut": "TIDAK_BERLAKU",
}


class GraphDatabaseError(Exception):
    """Raised when graph database operation fails."""
    pass


class GraphService:
    """Neo4j graph database manager for legal relationships."""

    def __init__(self, config: Optional[Config] = None):
        """Initialize graph service.

        Args:
            config: Configuration object (uses default if not provided)
        """
        self.config = config or default_config
        self._driver: Optional[Driver] = None

    def connect(self) -> bool:
        """Connect to Neo4j database.

        Returns:
            True if connected successfully, False otherwise
        """
        if not self.config.neo4j_enabled:
            logger.info("Neo4j is disabled in config")
            return False

        if not _ensure_neo4j():
            logger.warning("Neo4j package not available")
            return False

        try:
            self._driver = _GraphDatabase.driver(
                self.config.neo4j_uri,
                auth=(self.config.neo4j_user, self.config.neo4j_password),
            )
            # Verify connection
            self._driver.verify_connectivity()
            logger.info(f"Connected to Neo4j: {self.config.neo4j_uri}")
            return True

        except _ServiceUnavailable as e:
            logger.warning(f"Neo4j not available: {e}")
            return False
        except _AuthError as e:
            logger.error(f"Neo4j auth failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Neo4j connection error: {e}")
            return False

    def close(self) -> None:
        """Close Neo4j connection."""
        if self._driver:
            self._driver.close()
            self._driver = None
            logger.info("Neo4j connection closed")

    @property
    def is_connected(self) -> bool:
        """Check if connected to Neo4j."""
        return self._driver is not None

    @contextmanager
    def session(self):
        """Get a Neo4j session context manager.

        Yields:
            Neo4j session
        """
        if not self._driver:
            raise GraphDatabaseError("Not connected to Neo4j")

        session = self._driver.session()
        try:
            yield session
        finally:
            session.close()

    def upsert_peraturan(self, peraturan: Peraturan) -> None:
        """Insert or update a Peraturan node.

        Args:
            peraturan: Peraturan instance to save
        """
        if not self._driver:
            return

        # Map status
        status = STATUS_MAP.get(peraturan.status, "UNCERTAIN")

        query = """
        MERGE (p:Peraturan {slug: $slug})
        SET p.jenis = $jenis,
            p.nomor = $nomor,
            p.tahun = $tahun,
            p.tentang = $tentang,
            p.status = $status,
            p.status_confidence = $confidence,
            p.pemrakarsa = $pemrakarsa,
            p.tanggal_penetapan = $tanggal_penetapan,
            p.tanggal_pengundangan = $tanggal_pengundangan,
            p.source_url = $source_url,
            p.updated_at = datetime()
        """

        with self.session() as session:
            session.run(query, {
                "slug": peraturan.slug,
                "jenis": peraturan.jenis,
                "nomor": peraturan.nomor,
                "tahun": peraturan.tahun,
                "tentang": peraturan.tentang,
                "status": status,
                "confidence": 0.95 if status in ("BERLAKU", "TIDAK_BERLAKU") else 0.5,
                "pemrakarsa": peraturan.pemrakarsa,
                "tanggal_penetapan": peraturan.tanggal_penetapan,
                "tanggal_pengundangan": peraturan.tanggal_pengundangan,
                "source_url": peraturan.source_url,
            })
            logger.debug(f"Upserted node: {peraturan.slug}")

    def create_relationship(
        self,
        from_slug: str,
        to_slug: str,
        rel_type: str,
        properties: Optional[dict] = None,
    ) -> None:
        """Create a relationship between two Peraturan nodes.

        Args:
            from_slug: Source peraturan slug
            to_slug: Target peraturan slug
            rel_type: Relationship type (MENCABUT, MENGUBAH, etc.)
            properties: Optional relationship properties
        """
        if not self._driver:
            return

        props = properties or {}

        # Dynamic relationship type requires APOC or string formatting
        # Using parameterized approach for safety
        query = f"""
        MATCH (a:Peraturan {{slug: $from_slug}})
        MATCH (b:Peraturan {{slug: $to_slug}})
        MERGE (a)-[r:{rel_type}]->(b)
        SET r += $props
        """

        with self.session() as session:
            session.run(query, {
                "from_slug": from_slug,
                "to_slug": to_slug,
                "props": props,
            })
            logger.debug(f"Created relationship: {from_slug} -[{rel_type}]-> {to_slug}")

    def create_mencabut(self, new_slug: str, old_slug: str, pasal_dasar: Optional[str] = None) -> None:
        """Create MENCABUT (revokes) relationship.

        Args:
            new_slug: New peraturan that revokes
            old_slug: Old peraturan being revoked
            pasal_dasar: Article basis for revocation
        """
        props = {"scope": "FULL"}
        if pasal_dasar:
            props["pasal_dasar"] = pasal_dasar

        self.create_relationship(new_slug, old_slug, "MENCABUT", props)

    def create_mengubah(
        self,
        amend_slug: str,
        original_slug: str,
        urutan: int = 1,
        pasal_diubah: Optional[list[str]] = None,
    ) -> None:
        """Create MENGUBAH (amends) relationship.

        Args:
            amend_slug: Amendment peraturan
            original_slug: Original peraturan being amended
            urutan: Amendment order (1st, 2nd, etc.)
            pasal_diubah: List of amended articles
        """
        props = {"urutan": urutan}
        if pasal_diubah:
            props["pasal_diubah"] = pasal_diubah

        self.create_relationship(amend_slug, original_slug, "MENGUBAH", props)

    def create_merujuk(
        self,
        from_slug: str,
        to_slug: str,
        pasal_sumber: Optional[str] = None,
        pasal_tujuan: Optional[str] = None,
        jenis: str = "UMUM",
    ) -> None:
        """Create MERUJUK (references) relationship.

        Args:
            from_slug: Referencing peraturan
            to_slug: Referenced peraturan
            pasal_sumber: Source article
            pasal_tujuan: Target article
            jenis: Reference type (DEFINISI, PROSEDUR, SANKSI, UMUM)
        """
        props = {"jenis": jenis}
        if pasal_sumber:
            props["pasal_sumber"] = pasal_sumber
        if pasal_tujuan:
            props["pasal_tujuan"] = pasal_tujuan

        self.create_relationship(from_slug, to_slug, "MERUJUK", props)

    def create_berlaku_bersyarat(
        self,
        old_slug: str,
        new_slug: str,
        kondisi: str,
        pasal_dasar: Optional[str] = None,
    ) -> None:
        """Create BERLAKU_BERSYARAT (conditionally valid) relationship.

        Args:
            old_slug: Old peraturan with conditional validity
            new_slug: New peraturan that imposes condition
            kondisi: Condition text
            pasal_dasar: Article basis
        """
        props = {
            "kondisi": kondisi,
            "conflict_detected": False,
        }
        if pasal_dasar:
            props["pasal_dasar"] = pasal_dasar

        self.create_relationship(old_slug, new_slug, "BERLAKU_BERSYARAT", props)

        # Update old peraturan status to CONDITIONAL
        self._update_status(old_slug, "CONDITIONAL", 0.6)

    def create_masa_peralihan(
        self,
        old_slug: str,
        new_slug: str,
        kondisi: str,
        successor_type: Optional[str] = None,
    ) -> None:
        """Create MASA_PERALIHAN (transitional) relationship.

        Args:
            old_slug: Old peraturan in transition
            new_slug: New peraturan
            kondisi: Transition condition
            successor_type: Expected successor type (PP, Perpres, etc.)
        """
        props = {
            "kondisi": kondisi,
            "is_satisfied": False,
        }
        if successor_type:
            props["successor_type"] = successor_type

        self.create_relationship(old_slug, new_slug, "MASA_PERALIHAN", props)

        # Update old peraturan status to TRANSITIONAL
        self._update_status(old_slug, "TRANSITIONAL", 0.7)

    def _update_status(self, slug: str, status: str, confidence: float) -> None:
        """Update peraturan status.

        Args:
            slug: Peraturan slug
            status: New status
            confidence: Confidence score
        """
        if not self._driver:
            return

        query = """
        MATCH (p:Peraturan {slug: $slug})
        SET p.status = $status,
            p.status_confidence = $confidence,
            p.needs_review = true
        """

        with self.session() as session:
            session.run(query, {
                "slug": slug,
                "status": status,
                "confidence": confidence,
            })

    def add_review_flag(self, slug: str, flag: str, description: str) -> None:
        """Add a review flag to a peraturan.

        Args:
            slug: Peraturan slug
            flag: Flag type (CONFLICTING_PROVISIONS, etc.)
            description: Flag description
        """
        if not self._driver:
            return

        query = """
        MATCH (p:Peraturan {slug: $slug})
        SET p.needs_review = true,
            p.review_flags = coalesce(p.review_flags, []) + $flag
        CREATE (r:ReviewRequest {
            id: randomUUID(),
            peraturan_slug: $slug,
            flag_type: $flag,
            description: $description,
            created_at: datetime(),
            status: 'PENDING'
        })
        """

        with self.session() as session:
            session.run(query, {
                "slug": slug,
                "flag": flag,
                "description": description,
            })
            logger.info(f"Added review flag {flag} for {slug}")

    def get_peraturan(self, slug: str) -> Optional[dict]:
        """Get a peraturan node by slug.

        Args:
            slug: Peraturan slug

        Returns:
            Node properties or None
        """
        if not self._driver:
            return None

        query = "MATCH (p:Peraturan {slug: $slug}) RETURN p"

        with self.session() as session:
            result = session.run(query, {"slug": slug})
            record = result.single()
            if record:
                return dict(record["p"])
        return None

    def get_relationships(self, slug: str) -> list[dict]:
        """Get all relationships for a peraturan.

        Args:
            slug: Peraturan slug

        Returns:
            List of relationship info
        """
        if not self._driver:
            return []

        query = """
        MATCH (p:Peraturan {slug: $slug})-[r]->(other:Peraturan)
        RETURN type(r) as rel_type, properties(r) as props, other.slug as target
        UNION
        MATCH (other:Peraturan)-[r]->(p:Peraturan {slug: $slug})
        RETURN type(r) as rel_type, properties(r) as props, other.slug as source
        """

        with self.session() as session:
            result = session.run(query, {"slug": slug})
            return [dict(record) for record in result]

    def get_statistics(self) -> dict:
        """Get graph statistics.

        Returns:
            Statistics dictionary
        """
        if not self._driver:
            return {}

        queries = {
            "total_nodes": "MATCH (p:Peraturan) RETURN count(p) as count",
            "total_relationships": "MATCH ()-[r]->() RETURN count(r) as count",
            "by_status": """
                MATCH (p:Peraturan)
                RETURN p.status as status, count(p) as count
                ORDER BY count DESC
            """,
            "by_rel_type": """
                MATCH ()-[r]->()
                RETURN type(r) as rel_type, count(r) as count
                ORDER BY count DESC
            """,
            "needs_review": """
                MATCH (p:Peraturan)
                WHERE p.needs_review = true
                RETURN count(p) as count
            """,
        }

        stats = {}
        with self.session() as session:
            for key, query in queries.items():
                result = session.run(query)
                if key == "by_status":
                    stats[key] = {r["status"]: r["count"] for r in result}
                elif key == "by_rel_type":
                    stats[key] = {r["rel_type"]: r["count"] for r in result}
                else:
                    record = result.single()
                    stats[key] = record["count"] if record else 0

        return stats
