"""Resolve registered targets without mixing source and host event sequences."""


def target_registrations(event):
    if event['kind'] == 'target_registered':
        return {event['entity_id']: event['payload']}
    from .arena_imports import IMPORT_KIND, validate_import_payload
    if event['kind'] != IMPORT_KIND:
        return {}
    payload = validate_import_payload(event['payload'])
    return {item['entity_id']: item['payload'] for item in payload['source_events']
            if item['kind'] == 'target_registered'}


def registered_targets(events):
    from .arena_imports import imported_streams
    imported_streams(events)
    return {ident: target for event in events for ident, target in target_registrations(event).items()}
