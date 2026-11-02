"""Pure selection of the option named by a plan's approval."""


def selected_option(plan: dict) -> dict:
    """Project the explicitly approved option, or the main option before approval."""
    selected = (plan.get('approval') or {}).get('option_id', plan['main_option'])
    return next(option for option in plan['options'] if option['id'] == selected)
