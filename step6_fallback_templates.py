def get_fallback_copy(action_card: dict, channel: str = "sms") -> dict:
    """
    Returns the deterministic fallback copy based on the action_type and segment.
    Only fills placeholders from evidence and policy (no LLM logic).
    """
    action_type = action_card.get('action_type', 'NO_ACTION')
    evidence = action_card.get('evidence', {})
    if isinstance(evidence, str):
        import json
        evidence = json.loads(evidence)
        
    segment = evidence.get('rfm_segment', 'Valued')
    product_id = evidence.get('product_id', 'product')
    discount_pct = action_card.get('parameter_value', 0.0)
    
    discount_text = f"{int(discount_pct * 100)}%"
    
    if action_type == "DISCOUNT":
        body = f"As a valued {segment} customer, enjoy {discount_text} off your next order of items like {product_id}."
        subj = f"Your {discount_text} Discount is Here"
    elif action_type == "BUNDLE":
        body = f"We put together a special bundle for {segment} customers. Grab {product_id} and save!"
        subj = "Special Bundle Offer"
    else:
        body = f"Hello from our team! Thanks for being a {segment} customer."
        subj = "Update from us"
        
    return {
        "channel": channel,
        "subject": subj if channel == "email" else None,
        "body_template": body
    }
