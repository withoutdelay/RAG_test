"""Add product models and interfaces tables."""

from __future__ import annotations

import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260419_0010"
down_revision = "20260419_0009"
branch_labels = None
depends_on = None


_DEFAULT_PRODUCT_MODELS = [
    {
        "series_code": "lci_sync_drive",
        "model_number": "GBT.LCI.SO-A0606-211N465",
        "rated_voltage": "10kV",
        "rated_power_kw": 4208.0,
        "rated_current": "277.5A",
        "source_material_key": "vera-46268861",
        "specs": {
            "equipment_role": "sfc_soft_start",
            "converter_current_a": 2180,
            "input_transformer_kva": 5458,
            "output_transformer_kva": 4807,
            "motor_current_a": 277.5,
            "duty_cycle": "5min ON / 55min OFF",
            "application": "high_consistency_refiner",
        },
    },
    {
        "series_code": "hv_vfd_multilevel",
        "model_number": "10kV-1D2-19600/8100",
        "rated_voltage": "10kV",
        "rated_power_kw": None,
        "rated_current": None,
        "source_material_key": "099-230101-001-20e2c4ea",
        "specs": {
            "application": "one_drive_two_air_compressor_booster",
            "motor_powers_kw": [19600, 8100],
            "quantity": 1,
            "cooling": "forced_air",
            "topology": "multi_level_cell_series",
            "rectifier_transformer": "dry_phase_shifting",
            "switchgear_current_a": 1250,
        },
    },
    {
        "series_code": "hv_solid_state_starter",
        "model_number": "GGQ-4000/10",
        "rated_voltage": "10kV",
        "rated_power_kw": 3100.0,
        "rated_current": None,
        "source_material_key": "10kv-2025-3-d52df5c4",
        "specs": {
            "load_type": "compressor",
            "quantity": 2,
            "bypass_mode": "close_when_current_below_rated",
            "switchgear_model": "KYN28-12",
        },
    },
    {
        "series_code": "autotransformer_starter",
        "model_number": "TBQ-16000/6",
        "rated_voltage": "6kV",
        "rated_power_kw": 15000.0,
        "rated_current": "1642A",
        "source_material_key": "v1-2024-10-14-4298bf85",
        "specs": {
            "motor_model": "YKS900-4",
            "predicted_start_time_s": 44,
            "start_current_control": "1.5Ie-2.5Ie",
            "control_modes": ["current_control", "time_control"],
            "communication": "RS485 Modbus",
        },
    },
    {
        "series_code": "liquid_resistor_starter",
        "model_number": "GSDQ-8000/10",
        "rated_voltage": "10kV",
        "rated_power_kw": 6300.0,
        "rated_current": None,
        "source_material_key": "sample-5d7c31fb",
        "specs": {
            "motor_type": "synchronous_motor",
            "voltage_range": "3-12kV",
            "power_range_kw": "200-20000",
            "start_current_limit": "2-3.5Ie",
            "continuous_starts": "2-3",
            "remote_monitoring": "RS485",
        },
    },
]


_DEFAULT_PRODUCT_INTERFACES = [
    {
        "series_code": "lci_sync_drive",
        "interface_type": "communication",
        "protocol": "Profibus-DP",
        "sort_order": 10,
        "source_material_key": "vera-46268861",
        "signal_spec": {
            "adapter": "fieldbus_adapter",
            "digital_input_voltage": "24VDC",
            "coverage": ["lci", "switchgear", "oil_station", "cooler", "excitation_cabinet"],
        },
        "notes": "来自湛江中纸 LCI 主样本，现场总线适配器暂定 Profibus-DP。",
    },
    {
        "series_code": "lci_sync_drive",
        "interface_type": "io_signal",
        "protocol": None,
        "sort_order": 20,
        "source_material_key": "vera-46268861",
        "signal_spec": {
            "analog_outputs": ["4-20mA oil_level", "4-20mA oil_temperature", "4-20mA supply_temperature"],
            "control_sequence": ["excitation_build_wait_5s", "sync_switching"],
            "supporting_systems": ["excitation_cabinet", "oil_station", "cooler"],
        },
        "notes": "当前只沉淀最小信号骨架，完整 I/O 点表仍需客户正式接口表补齐。",
    },
    {
        "series_code": "hv_vfd_multilevel",
        "interface_type": "communication",
        "protocol": "Modbus/RS485",
        "sort_order": 10,
        "source_material_key": "099-230101-001-20e2c4ea",
        "signal_spec": {
            "remote_speed_reference": ["4-20mA", "0-10V"],
            "analog_output_count": 4,
            "analog_feedback_candidates": ["output_frequency", "output_current", "output_voltage", "output_power"],
        },
        "notes": "秦风气体技术协议明确预留 Modbus/RS485，并允许 DCS 以模拟量给定调速。",
    },
    {
        "series_code": "hv_vfd_multilevel",
        "interface_type": "io_signal",
        "protocol": None,
        "sort_order": 20,
        "source_material_key": "099-230101-001-20e2c4ea",
        "signal_spec": {
            "dcs_outputs": [
                "alarm_fault",
                "standby_running_fault_status",
                "medium_voltage_close_permit",
                "medium_voltage_emergency_trip",
                "bypass_close_command",
            ],
            "dcs_inputs": ["medium_voltage_ready", "start", "stop", "emergency_stop"],
            "contact_rating": "AC220V 5A passive_contact",
        },
        "notes": "保留一拖二高压变频与中压柜/旁路柜之间的最小联锁信号。",
    },
    {
        "series_code": "hv_solid_state_starter",
        "interface_type": "io_signal",
        "protocol": None,
        "sort_order": 10,
        "source_material_key": "10kv-2025-3-d52df5c4",
        "signal_spec": {
            "start_sequence": ["upstream_breaker_close", "soft_start", "bypass_close_km1"],
            "load_feedback": ["current_below_rated"],
            "boundary": "cabinet_terminals_outside_by_owner",
        },
        "notes": "荣信样本明确存在旁路合闸指令，当前按最小硬接点/联锁接口建模。",
    },
    {
        "series_code": "autotransformer_starter",
        "interface_type": "communication",
        "protocol": "Modbus/RS485",
        "sort_order": 10,
        "source_material_key": "v1-2024-10-14-4298bf85",
        "signal_spec": {
            "controller": "PLC",
            "hmi": "touch_screen",
            "capabilities": ["remote_monitoring", "parameter_storage", "curve_optimization"],
        },
        "notes": "TBQ 样本明确具备 RS485 + Modbus 和本地触摸屏控制。",
    },
    {
        "series_code": "autotransformer_starter",
        "interface_type": "io_signal",
        "protocol": None,
        "sort_order": 20,
        "source_material_key": "v1-2024-10-14-4298bf85",
        "signal_spec": {
            "inputs": ["start_breaker_close_signal", "run_breaker_work_position", "run_breaker_close_feedback"],
            "outputs": ["start_permit", "trip_command", "run_breaker_close_command"],
            "effective_logic": "DI_DO_closed_valid",
        },
        "notes": "由调压控制柜 PLC 与起动柜/运行柜之间的联锁信号抽象而来。",
    },
    {
        "series_code": "liquid_resistor_starter",
        "interface_type": "communication",
        "protocol": "RS485",
        "sort_order": 10,
        "source_material_key": "sample-5d7c31fb",
        "signal_spec": {
            "controller": "PLC",
            "capabilities": ["remote_monitoring", "centralized_control"],
        },
        "notes": "液阻软起样本明确保留 RS485 远程监控接口。",
    },
    {
        "series_code": "liquid_resistor_starter",
        "interface_type": "io_signal",
        "protocol": None,
        "sort_order": 20,
        "source_material_key": "sample-5d7c31fb",
        "signal_spec": {
            "interlock_signals": ["start_permit", "running", "start_timeout_alarm", "common_fault"],
            "signal_mode": "dry_contact",
            "requires": ["switchgear_interlock"],
        },
        "notes": "当前按液阻软起与上位机/高压柜之间的最小干接点信号归档。",
    },
]


def upgrade() -> None:
    op.create_table(
        "product_models",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("catalog_version", sa.String(length=80), nullable=False),
        sa.Column("is_published", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "series_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product_series.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("series_code", sa.String(length=120), nullable=False),
        sa.Column("model_number", sa.String(length=160), nullable=False),
        sa.Column("rated_voltage", sa.String(length=40), nullable=True),
        sa.Column("rated_power_kw", sa.Float(), nullable=True),
        sa.Column("rated_current", sa.String(length=80), nullable=True),
        sa.Column("specs", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("source_material_key", sa.String(length=160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint(
            "catalog_version",
            "series_code",
            "model_number",
            name="uq_product_models_version_series_model",
        ),
    )
    op.create_index(
        "idx_product_models_catalog_series_published",
        "product_models",
        ["catalog_version", "is_published", "series_code"],
    )

    op.create_table(
        "product_interfaces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("catalog_version", sa.String(length=80), nullable=False),
        sa.Column("is_published", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "series_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product_series.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("series_code", sa.String(length=120), nullable=False),
        sa.Column("interface_type", sa.String(length=40), nullable=False),
        sa.Column("protocol", sa.String(length=80), nullable=True),
        sa.Column(
            "signal_spec",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("source_material_key", sa.String(length=160), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint(
            "catalog_version",
            "series_code",
            "interface_type",
            "sort_order",
            name="uq_product_interfaces_version_series_slot",
        ),
    )
    op.create_index(
        "idx_product_interfaces_catalog_series_published",
        "product_interfaces",
        ["catalog_version", "is_published", "series_code"],
    )

    bind = op.get_bind()
    series = sa.table(
        "product_series",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("catalog_version", sa.String(length=80)),
        sa.column("is_published", sa.Boolean()),
        sa.column("code", sa.String(length=120)),
    )
    model_table = sa.table(
        "product_models",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("catalog_version", sa.String(length=80)),
        sa.column("is_published", sa.Boolean()),
        sa.column("series_id", postgresql.UUID(as_uuid=True)),
        sa.column("series_code", sa.String(length=120)),
        sa.column("model_number", sa.String(length=160)),
        sa.column("rated_voltage", sa.String(length=40)),
        sa.column("rated_power_kw", sa.Float()),
        sa.column("rated_current", sa.String(length=80)),
        sa.column("specs", postgresql.JSONB(astext_type=sa.Text())),
        sa.column("source_material_key", sa.String(length=160)),
    )
    interface_table = sa.table(
        "product_interfaces",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("catalog_version", sa.String(length=80)),
        sa.column("is_published", sa.Boolean()),
        sa.column("series_id", postgresql.UUID(as_uuid=True)),
        sa.column("series_code", sa.String(length=120)),
        sa.column("interface_type", sa.String(length=40)),
        sa.column("protocol", sa.String(length=80)),
        sa.column("signal_spec", postgresql.JSONB(astext_type=sa.Text())),
        sa.column("notes", sa.Text()),
        sa.column("source_material_key", sa.String(length=160)),
        sa.column("sort_order", sa.Integer()),
    )

    series_rows = bind.execute(
        sa.select(series.c.id, series.c.catalog_version, series.c.is_published, series.c.code)
    ).fetchall()
    series_map = {(str(row.catalog_version), str(row.code)): row for row in series_rows}

    model_rows: list[dict[str, object]] = []
    interface_rows: list[dict[str, object]] = []
    for version in sorted({str(row.catalog_version) for row in series_rows}):
        for item in _DEFAULT_PRODUCT_MODELS:
            series_row = series_map.get((version, str(item["series_code"])))
            if not series_row:
                continue
            model_rows.append(
                {
                    "id": uuid.uuid4(),
                    "catalog_version": version,
                    "is_published": bool(series_row.is_published),
                    "series_id": series_row.id,
                    "series_code": item["series_code"],
                    "model_number": item["model_number"],
                    "rated_voltage": item["rated_voltage"],
                    "rated_power_kw": item["rated_power_kw"],
                    "rated_current": item["rated_current"],
                    "specs": item["specs"],
                    "source_material_key": item["source_material_key"],
                }
            )
        for item in _DEFAULT_PRODUCT_INTERFACES:
            series_row = series_map.get((version, str(item["series_code"])))
            if not series_row:
                continue
            interface_rows.append(
                {
                    "id": uuid.uuid4(),
                    "catalog_version": version,
                    "is_published": bool(series_row.is_published),
                    "series_id": series_row.id,
                    "series_code": item["series_code"],
                    "interface_type": item["interface_type"],
                    "protocol": item["protocol"],
                    "signal_spec": item["signal_spec"],
                    "notes": item["notes"],
                    "source_material_key": item["source_material_key"],
                    "sort_order": item["sort_order"],
                }
            )

    if model_rows:
        op.bulk_insert(model_table, model_rows)
    if interface_rows:
        op.bulk_insert(interface_table, interface_rows)


def downgrade() -> None:
    op.drop_index("idx_product_interfaces_catalog_series_published", table_name="product_interfaces")
    op.drop_table("product_interfaces")
    op.drop_index("idx_product_models_catalog_series_published", table_name="product_models")
    op.drop_table("product_models")
