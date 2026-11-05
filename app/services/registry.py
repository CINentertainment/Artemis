from app.config import Settings
from app.schemas import ServiceDefinition


def build_service_registry(
    settings: Settings,
) -> list[ServiceDefinition]:
    return [
        ServiceDefinition(
            key="odysseus",
            name="Odysseus Workspace",
            category="temporary-interface",
            base_url=settings.odysseus_url,
            description=(
                "Temporary legacy workspace used during "
                "the Artemis transition."
            ),
            required=False,
        ),
        ServiceDefinition(
            key="chroma",
            name="Artemis Semantic Memory",
            category="memory",
            base_url=settings.chroma_url,
            description=(
                "Local vector database supporting "
                "semantic memory retrieval."
            ),
            required=True,
        ),
        ServiceDefinition(
            key="intel-notes",
            name="Intel Notes Manager",
            category="intelligence",
            base_url=settings.intel_notes_url,
            description=(
                "Structured intelligence notes, source "
                "evaluations, and analytical records."
            ),
            required=False,
        ),
        ServiceDefinition(
            key="risk-tracker",
            name="Protective Intelligence Risk Tracker",
            category="protective-intelligence",
            base_url=settings.risk_tracker_url,
            description=(
                "Protective-intelligence case management "
                "and risk-assessment service."
            ),
            required=False,
        ),
        ServiceDefinition(
            key="market-lens",
            name="Market Lens",
            category="market-intelligence",
            base_url=settings.market_lens_url,
            description=(
                "Market monitoring, analytics, signals, "
                "and paper-portfolio service."
            ),
            required=False,
        ),
    ]