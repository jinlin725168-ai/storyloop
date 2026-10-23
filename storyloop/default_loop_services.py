"""Default role request assembly, separate from the injectable agent protocol."""
from .run_services import LoopServices
from .semantic_repair import REPAIR_KEYS
from .smoke import suite_relative


def _default_services() -> LoopServices:
    from .project_agents import invoke_project_agent
    return LoopServices(
        generate_smoke=lambda project, state: invoke_project_agent(
            project, "acceptance",
            {"change": {key: value for key, value in state.items() if key not in REPAIR_KEYS},
             "suite_root": suite_relative(project, state["change_id"]),
             **{key: state[key] for key in REPAIR_KEYS if key in state}}),
        implement=lambda project, context: invoke_project_agent(project, "implement", context),
        review=lambda project, context: invoke_project_agent(project, "review", context),
        characterize=lambda project, context: invoke_project_agent(project, "characterize", context),
        design=lambda project, context: invoke_project_agent(project, "design", context),
    )
