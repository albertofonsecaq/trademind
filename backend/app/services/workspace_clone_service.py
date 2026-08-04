"""
Workspace clone service.

Copies the full knowledge base of a source workspace to a new workspace
owned by a different user. Platform credentials, billing records, and
user-personal data are never copied.

See trading-knowledge-base-spec.md §15 "Workspace Clone" for the full
invariant list.
"""
from __future__ import annotations
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember
from app.models.source_config import SourceConfig
from app.models.evidence_item import EvidenceItem
from app.models.embedding_row import EmbeddingRow
from app.models.trade_idea import TradeIdea
from app.models.outcome_check import OutcomeCheck
from app.models.strategy_card import StrategyCard


async def clone_workspace(
    db: AsyncSession,
    *,
    source_workspace_id: uuid.UUID,
    target_user_id: uuid.UUID,
    new_name: str | None,
) -> Workspace:
    """
    Clone source workspace's knowledge base into a new workspace owned by
    target_user_id. The caller is responsible for committing.
    """
    source = await db.get(Workspace, source_workspace_id)
    if not source:
        raise ValueError(f"Source workspace {source_workspace_id} not found")

    # ── 1. New workspace ───────────────────────────────────────────────────────
    new_ws = Workspace(
        name=new_name or f"{source.name} (copy)",
        owner_user_id=target_user_id,
        topic_scope=source.topic_scope,
        payment_enabled=False,
        monthly_budget_cap=None,
    )
    db.add(new_ws)
    await db.flush()

    # ── 2. Owner membership ────────────────────────────────────────────────────
    db.add(WorkspaceMember(
        workspace_id=new_ws.id,
        user_id=target_user_id,
        role="owner",
    ))

    # ── 3. Source configs (reset fetch state, no platform connection) ──────────
    sc_result = await db.execute(
        select(SourceConfig).where(SourceConfig.workspace_id == source_workspace_id)
    )
    src_configs = sc_result.scalars().all()

    source_config_id_map: dict[uuid.UUID, uuid.UUID] = {}
    new_src_configs = []
    for sc in src_configs:
        new_id = uuid.uuid4()
        source_config_id_map[sc.id] = new_id
        new_src_configs.append(SourceConfig(
            id=new_id,
            workspace_id=new_ws.id,
            platform_connection_id=None,
            source_type=sc.source_type,
            identifier=sc.identifier,
            label=sc.label,
            fetch_cadence=sc.fetch_cadence,
            content_filters=sc.content_filters,
            backfill_start_date=None,
            last_fetched_id=None,
            last_fetched_at=None,
            created_by_user_id=target_user_id,
        ))
    db.add_all(new_src_configs)
    await db.flush()

    # ── 4. Evidence items ──────────────────────────────────────────────────────
    ei_result = await db.execute(
        select(EvidenceItem).where(EvidenceItem.workspace_id == source_workspace_id)
    )
    evidence_items = ei_result.scalars().all()

    evidence_id_map: dict[uuid.UUID, uuid.UUID] = {}
    new_evidence = []
    for ei in evidence_items:
        new_id = uuid.uuid4()
        evidence_id_map[ei.id] = new_id
        new_evidence.append(EvidenceItem(
            id=new_id,
            workspace_id=new_ws.id,
            source_config_id=source_config_id_map.get(ei.source_config_id),
            stable_id=ei.stable_id,
            source_type=ei.source_type,
            type=ei.type,
            content=ei.content,
            confidence=ei.confidence,
            original_language=ei.original_language,
            is_on_topic=ei.is_on_topic,
            relevance_reason=ei.relevance_reason,
            source_metadata=ei.source_metadata,
            message_timestamp=ei.message_timestamp,
        ))
    db.add_all(new_evidence)
    await db.flush()

    # ── 5. Embedding rows ──────────────────────────────────────────────────────
    er_result = await db.execute(
        select(EmbeddingRow).where(EmbeddingRow.workspace_id == source_workspace_id)
    )
    embedding_rows = er_result.scalars().all()

    new_embeddings = []
    for er in embedding_rows:
        new_embeddings.append(EmbeddingRow(
            workspace_id=new_ws.id,
            source_type=er.source_type,
            source_id=er.source_id,
            text=er.text,
            embedding=er.embedding,
            source_language=er.source_language,
            embedding_metadata=er.embedding_metadata,
        ))
    db.add_all(new_embeddings)
    await db.flush()

    # ── 6. Trade ideas ─────────────────────────────────────────────────────────
    ti_result = await db.execute(
        select(TradeIdea).where(TradeIdea.workspace_id == source_workspace_id)
    )
    trade_ideas = ti_result.scalars().all()

    trade_idea_id_map: dict[uuid.UUID, uuid.UUID] = {}
    new_ideas = []
    for ti in trade_ideas:
        new_id = uuid.uuid4()
        trade_idea_id_map[ti.id] = new_id
        new_ideas.append(TradeIdea(
            id=new_id,
            workspace_id=new_ws.id,
            evidence_item_id=evidence_id_map.get(ti.evidence_item_id, ti.evidence_item_id),
            source_language=ti.source_language,
            symbol=ti.symbol,
            setup_type=ti.setup_type,
            action=ti.action,
            entry=ti.entry,
            target=ti.target,
            stop=ti.stop,
            summary_en=ti.summary_en,
            summary_es=ti.summary_es,
            original_text=ti.original_text,
            author=ti.author,
            channel=ti.channel,
            message_timestamp=ti.message_timestamp,
        ))
    db.add_all(new_ideas)
    await db.flush()

    # ── 7. Outcome checks ──────────────────────────────────────────────────────
    oc_result = await db.execute(
        select(OutcomeCheck).where(OutcomeCheck.workspace_id == source_workspace_id)
    )
    outcome_checks = oc_result.scalars().all()

    new_checks = []
    for oc in outcome_checks:
        new_checks.append(OutcomeCheck(
            workspace_id=new_ws.id,
            trade_idea_id=trade_idea_id_map.get(oc.trade_idea_id, oc.trade_idea_id),
            symbol=oc.symbol,
            entry_price=oc.entry_price,
            target_price=oc.target_price,
            stop_price=oc.stop_price,
            action=oc.action,
            trade_date=oc.trade_date,
            outcome=oc.outcome,
            outcome_date=oc.outcome_date,
            holding_days=oc.holding_days,
            max_adverse_excursion=oc.max_adverse_excursion,
            max_favorable_excursion=oc.max_favorable_excursion,
            data_source=oc.data_source,
            window_days=oc.window_days,
        ))
    db.add_all(new_checks)
    await db.flush()

    # ── 8. Strategy cards (remap supporting_evidence UUID strings) ─────────────
    card_result = await db.execute(
        select(StrategyCard).where(StrategyCard.workspace_id == source_workspace_id)
    )
    strategy_cards = card_result.scalars().all()

    new_cards = []
    for sc in strategy_cards:
        remapped_evidence = [
            str(trade_idea_id_map.get(uuid.UUID(uid), uuid.UUID(uid)))
            for uid in (sc.supporting_evidence or [])
            if _is_valid_uuid(uid)
        ]
        new_cards.append(StrategyCard(
            workspace_id=new_ws.id,
            setup_type=sc.setup_type,
            symbol_scope=sc.symbol_scope,
            description_en=sc.description_en,
            description_es=sc.description_es,
            flowchart_spec=sc.flowchart_spec,
            supporting_evidence=remapped_evidence,
            win_rate=sc.win_rate,
            sample_size=sc.sample_size,
            confidence_interval=sc.confidence_interval,
            preliminary_confidence=sc.preliminary_confidence,
            source_count=sc.source_count,
            version=sc.version,
            version_history=sc.version_history,
            confidence_tier=sc.confidence_tier,
            walk_forward_result=sc.walk_forward_result,
            validation_updated_at=sc.validation_updated_at,
        ))
    db.add_all(new_cards)

    return new_ws


def _is_valid_uuid(val: str) -> bool:
    try:
        uuid.UUID(val)
        return True
    except (ValueError, AttributeError):
        return False
