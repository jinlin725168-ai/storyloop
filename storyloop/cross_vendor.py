"""Cross-vendor availability policy; capabilities never substitute for actual usage telemetry."""
from __future__ import annotations

import time

from .models import StoryLoopError, require
from .role_profiles import role_settings
from .telemetry import record_call


def policy(config: dict) -> dict:
    """Validate opt-in review availability policy."""
    value = config.get('cross_vendor_review', {})
    require(isinstance(value, dict), 'Invalid cross_vendor_review')
    require(isinstance(value.get('enabled', False), bool), 'Invalid cross_vendor_review.enabled')
    require(value.get('on_unavailable', 'fail_closed') in ('fail_closed', 'degrade'),
            'Invalid cross_vendor_review.on_unavailable')
    return {'enabled': value.get('enabled', False),
            'on_unavailable': value.get('on_unavailable', 'fail_closed')}


def require_distinct(config: dict, capabilities: dict) -> None:
    """Explicit adapters check requires two known, distinct handshake vendors."""
    if not policy(config)['enabled']:
        return
    names = [role_settings(config, role, None)['adapter'] for role in ('implement', 'review')]
    vendors = [capabilities.get(name, {}).get('vendor') for name in names]
    if not all(vendors):
        unknown = [f"{name}: {capabilities.get(name, {}).get('reason', 'vendor missing')}"
                   for name in names if not capabilities.get(name, {}).get('vendor')]
        raise StoryLoopError('Cross-vendor review handshake unavailable; fix adapter handshake/config: '
                             + '; '.join(unknown), code='AGENT_UNAVAILABLE',
                             details={'cross_vendor': True, 'role': 'review'})
    require(vendors[0].casefold() != vendors[1].casefold(),
            'Cross-vendor review requires different vendors')


def invoke(project, role, payload, config, ledger, call):
    """Route a call and, only by explicit policy, retry review on the implement adapter."""
    from .adapter_capabilities import ensure_checked
    settings = role_settings(config, role, payload.get('routing_tier') or payload.get('tier'))
    from .review_context import verification_active
    verifying = role == 'review' and verification_active()
    if verifying:
        settings = {**settings, 'tier': 'strong'}
    active = policy(config)['enabled'] and role == 'review'
    started = time.monotonic()
    try:
        ensure_checked(project)
        if verifying:
            from .adapter_capabilities import require_role_target
            require_role_target(project, config, role, settings)
    except StoryLoopError as error:
        if error.code != 'AGENT_UNAVAILABLE' or not error.details.get('cross_vendor'):
            raise
        if not active:
            return call(settings, None)
        # The failed availability request is a real attempt even though no role answer exists.
        record_call(project, role, ledger, {}, int((time.monotonic() - started) * 1000), failed=True,
                    engine_fields={'adapter': settings['adapter'], 'tier_requested': settings['tier']})
        return _fallback(project, role, payload, config, settings, call, error)
    try:
        return call(settings, None)
    except StoryLoopError as error:
        if not active or error.code != 'AGENT_UNAVAILABLE':
            raise
        return _fallback(project, role, payload, config, settings, call, error)


def _fallback(project, role, payload, config, settings, call, error):
    error.details.update(cross_vendor=True, role='review')
    if policy(config)['on_unavailable'] == 'fail_closed':
        raise error
    fallback = role_settings(config, 'implement', payload.get('routing_tier') or payload.get('tier'))
    settings = {**settings, 'adapter': fallback['adapter']}
    from .adapter_capabilities import require_role_target
    require_role_target(project, config, role, settings)
    try:
        return call(settings, 'cross_vendor')
    except StoryLoopError as fallback_error:
        if fallback_error.code == 'AGENT_UNAVAILABLE':
            fallback_error.details.update(cross_vendor=True, role='review')
        raise


def prepare_run(project, config):
    """Warm the engine capability cache before entering agent read-only boundaries."""
    from .adapter_capabilities import ensure_for_run
    from .role_profiles import enabled
    if not enabled(config):
        return
    try:
        ensure_for_run(project)
    except StoryLoopError as error:
        # An unavailable review vendor is handled at the review call under its explicit policy.
        # Configuration/profile errors still block before any work starts.
        if error.code != 'AGENT_UNAVAILABLE' or not error.details.get('cross_vendor'):
            raise
