import logging

from django.db.utils import DatabaseError, OperationalError, ProgrammingError
from django.db.models.signals import pre_migrate

from apps.core.services.db_sequence_repair import repair_postgres_sequences


logger = logging.getLogger(__name__)


def _repair_sequences_before_migrate(sender, using, **kwargs):
    try:
        repaired = repair_postgres_sequences(using=using)
    except (OperationalError, ProgrammingError, DatabaseError) as exc:
        logger.warning("Skipped pre-migrate sequence repair on %s: %s", using, exc)
        return
    except Exception as exc:
        logger.exception("Unexpected pre-migrate sequence repair error on %s: %s", using, exc)
        return

    if repaired:
        logger.info("Pre-migrate sequence repair aligned %s sequences on %s.", repaired, using)


pre_migrate.connect(
    _repair_sequences_before_migrate,
    dispatch_uid="core.pre_migrate.repair_sequences",
)
