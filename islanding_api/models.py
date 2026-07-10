import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# All `time` columns are TIMESTAMPTZ in Postgres (see init-db/001_schema.sql).
# DateTime(timezone=True) is required here - without it SQLAlchemy infers a
# naive TIMESTAMP type and asyncpg will reject timezone-aware Python datetimes
# (e.g. datetime.now(timezone.utc)) with a DataError at insert time.
TZDateTime = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


class GridState(str, enum.Enum):
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"
    FAULT_IMMINENT = "fault_imminent"
    ISLANDED = "islanded"


class LoadType(str, enum.Enum):
    CRITICAL = "critical"
    NON_CRITICAL = "non_critical"


# Shared enum column type. values_callable makes SQLAlchemy persist the lowercase
# .value ("normal") instead of the default uppercase member .name ("NORMAL") -
# without this it will NOT match the Postgres enum created in 001_schema.sql.
grid_state_pg_enum = SAEnum(
    GridState,
    name="grid_state_enum",
    values_callable=lambda obj: [e.value for e in obj],
)
load_type_pg_enum = SAEnum(
    LoadType,
    name="load_type_enum",
    values_callable=lambda obj: [e.value for e in obj],
)


class FeatureReading(Base):
    """FS-6/7/8/11/12: electrical features streamed from the edge MCU."""

    __tablename__ = "feature_readings"

    time: Mapped[datetime] = mapped_column(TZDateTime, primary_key=True, server_default=func.now())
    node_id: Mapped[str] = mapped_column(String, primary_key=True)
    voltage: Mapped[Optional[float]] = mapped_column(Float)
    current: Mapped[Optional[float]] = mapped_column(Float)
    frequency: Mapped[Optional[float]] = mapped_column(Float)
    fault_probability: Mapped[Optional[float]] = mapped_column(Float)
    soc: Mapped[Optional[float]] = mapped_column(Float)
    season: Mapped[Optional[str]] = mapped_column(String)
    environment: Mapped[Optional[dict]] = mapped_column(JSONB)


class AnomalyScore(Base):
    """FS-13/14: anomaly detection output (teammate's layer)."""

    __tablename__ = "anomaly_scores"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    time: Mapped[datetime] = mapped_column(TZDateTime, primary_key=True, server_default=func.now())
    node_id: Mapped[Optional[str]] = mapped_column(String)
    anomaly_score: Mapped[float] = mapped_column(Float, nullable=False)
    model_version: Mapped[Optional[str]] = mapped_column(String)


class GridStateLog(Base):
    """FS-16/17/18: grid state classification output."""

    __tablename__ = "grid_states"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    time: Mapped[datetime] = mapped_column(TZDateTime, primary_key=True, server_default=func.now())
    state: Mapped[GridState] = mapped_column(grid_state_pg_enum, nullable=False)
    fault_probability: Mapped[Optional[float]] = mapped_column(Float)
    anomaly_score: Mapped[Optional[float]] = mapped_column(Float)


class Decision(Base):
    """FS-19/20/21, NFS-9: decision-making layer (Devrim's layer)."""

    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    time: Mapped[datetime] = mapped_column(TZDateTime, primary_key=True, server_default=func.now())
    grid_state: Mapped[GridState] = mapped_column(grid_state_pg_enum, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    latency_ms: Mapped[Optional[float]] = mapped_column(Float)
    outcome: Mapped[Optional[str]] = mapped_column(String)
    features: Mapped[Optional[dict]] = mapped_column(JSONB)
    load_actions: Mapped[Optional[dict]] = mapped_column(JSONB)


class BatteryStatus(Base):
    """FS-4/5: battery priority + SOC tracking."""

    __tablename__ = "battery_status"

    time: Mapped[datetime] = mapped_column(TZDateTime, primary_key=True, server_default=func.now())
    battery_id: Mapped[str] = mapped_column(String, primary_key=True)
    soc: Mapped[float] = mapped_column(Float, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class LoadStatus(Base):
    """FS-9/10/22: load shedding + staggered reconnection tracking."""

    __tablename__ = "load_status"

    time: Mapped[datetime] = mapped_column(TZDateTime, primary_key=True, server_default=func.now())
    load_id: Mapped[str] = mapped_column(String, primary_key=True)
    load_type: Mapped[LoadType] = mapped_column(load_type_pg_enum, nullable=False)
    connected: Mapped[bool] = mapped_column(Boolean, nullable=False)
    priority_level: Mapped[Optional[int]] = mapped_column(Integer)


# NOTE: tables are created by init-db/001_schema.sql, not Base.metadata.create_all().
# This is required because hypertable conversion (SELECT create_hypertable(...))
# isn't something SQLAlchemy can express - these models are for ORM
# querying/inserting against that existing schema, not schema creation.
