import json
import re

# Exact error codes defined in requirements
class ErrorCodes:
    # Gate A Schema
    E_JSON_PARSE = "E_JSON_PARSE"
    E_SCHEMA_INVALID = "E_SCHEMA_INVALID"
    E_SCHEMA_MISSING_FIELD = "E_SCHEMA_MISSING_FIELD"
    E_SCHEMA_ENUM_VIOLATION = "E_SCHEMA_ENUM_VIOLATION"
    
    # Gate B Policy echo
    E_POLICY_ECHO_ACTION_MISMATCH = "E_POLICY_ECHO_ACTION_MISMATCH"
    E_POLICY_ECHO_DISCOUNT_MISMATCH = "E_POLICY_ECHO_DISCOUNT_MISMATCH"
    E_TRADE_ID_MISMATCH = "E_TRADE_ID_MISMATCH"
    
    # Gate C Grounding
    E_GROUNDING_UNKNOWN_FIELD = "E_GROUNDING_UNKNOWN_FIELD"
    E_GROUNDING_VALUE_MISMATCH = "E_GROUNDING_VALUE_MISMATCH"
    E_NUMERIC_INJECTION = "E_NUMERIC_INJECTION"
    E_GROUNDING_TOO_THIN = "E_GROUNDING_TOO_THIN"
    
    # Gate D Copy safety
    E_COPY_TOO_LONG = "E_COPY_TOO_LONG"
    E_COPY_BANNED_PHRASE = "E_COPY_BANNED_PHRASE"
    E_COPY_BAD_PLACEHOLDER = "E_COPY_BAD_PLACEHOLDER"
    E_COPY_CHANNEL_REQUIREMENT_FAIL = "E_COPY_CHANNEL_REQUIREMENT_FAIL"


class LLMSafetyGateway:
    def __init__(self, channel_rules_path="config/channel_rules.json", banned_phrases_path="config/banned_phrases.txt"):
        try:
            with open(channel_rules_path, "r") as f:
                self.channel_rules = json.load(f)
        except Exception:
            self.channel_rules = {
                "sms": {"max_length": 160, "required_elements": []},
                "email": {"max_length": 500, "required_elements": []}
            }
            
        try:
            with open(banned_phrases_path, "r") as f:
                self.banned_phrases = [line.strip().lower() for line in f if line.strip()]
        except Exception:
            self.banned_phrases = ["guarantee", "spam", "100% free"]
            
        self.allowed_placeholders = {"{PRODUCT}", "{DISCOUNT_TEXT}", "{CTA}", "{SEGMENT_LABEL}"}

    def validate(self, llm_response_text: str, action_card: dict) -> list[str]:
        """
        Runs the 4 gates.
        Returns a list of error codes. If empty, the validation passed.
        """
        errors = []
        
        # GATE A: Schema Validation
        try:
            llm_json = json.loads(llm_response_text)
        except json.JSONDecodeError:
            errors.append(ErrorCodes.E_JSON_PARSE)
            return errors # Fatal
            
        # Basic field checks
        required_fields = ["action_trade_id", "policy_echo", "grounding", "copy"]
        for req in required_fields:
            if req not in llm_json:
                errors.append(ErrorCodes.E_SCHEMA_MISSING_FIELD)
                return errors
                
        # Validate Enum
        copy_channel = llm_json["copy"].get("channel")
        if copy_channel not in ["sms", "email"]:
            errors.append(ErrorCodes.E_SCHEMA_ENUM_VIOLATION)
            
        action_type = llm_json["policy_echo"].get("action_type")
        if action_type not in ["DISCOUNT", "BUNDLE", "NO_ACTION"]:
            errors.append(ErrorCodes.E_SCHEMA_ENUM_VIOLATION)

        # Allow continuing if schema had soft errors (e.g. enum violation) but fields exist
        if ErrorCodes.E_SCHEMA_MISSING_FIELD in errors or ErrorCodes.E_SCHEMA_ENUM_VIOLATION in errors:
            return errors 

        # GATE B: Policy Echo
        input_trade_id = action_card.get("id")
        if llm_json["action_trade_id"] != input_trade_id:
            errors.append(ErrorCodes.E_TRADE_ID_MISMATCH)
            
        expected_discount = float(action_card.get("parameter_value", 0.0))
        echo_discount = float(llm_json["policy_echo"].get("discount_pct", -1.0))
        
        if abs(expected_discount - echo_discount) > 0.001:
            errors.append(ErrorCodes.E_POLICY_ECHO_DISCOUNT_MISMATCH)
            
        expected_action = action_card.get("action_type")
        if llm_json["policy_echo"].get("action_type") != expected_action:
            errors.append(ErrorCodes.E_POLICY_ECHO_ACTION_MISMATCH)

        # GATE C: Grounding
        evidence = action_card.get("evidence", {})
        if isinstance(evidence, str):
            evidence = json.loads(evidence)
            
        facts_used = llm_json["grounding"].get("facts_used", [])
        if not facts_used:
             errors.append(ErrorCodes.E_GROUNDING_TOO_THIN)
             
        for fact in facts_used:
            fld = fact.get("field")
            val = fact.get("value")
            if fld not in evidence:
                errors.append(ErrorCodes.E_GROUNDING_UNKNOWN_FIELD)
            else:
                # Type safe comparison
                if str(evidence[fld]) != str(val):
                    errors.append(ErrorCodes.E_GROUNDING_VALUE_MISMATCH)

        # Gate C part 2: No raw numeric discount injection
        body_text = llm_json["copy"].get("body_template", "")
        # Find raw percentages like 15%, 20%
        raw_pcts = re.findall(r"\b\d+%", body_text)
        if raw_pcts:
            errors.append(ErrorCodes.E_NUMERIC_INJECTION)

        # GATE D: Copy Safety
        channel = llm_json["copy"].get("channel", "sms")
        max_len = self.channel_rules.get(channel, {}).get("max_length", 160)
        
        if len(body_text) > max_len:
            errors.append(ErrorCodes.E_COPY_TOO_LONG)
            
        lower_body = body_text.lower()
        for phrase in self.banned_phrases:
            if phrase in lower_body:
                errors.append(ErrorCodes.E_COPY_BANNED_PHRASE)
                
        # Placeholder check
        found_placeholders = set(re.findall(r"\{[A-Z_]+\}", body_text))
        for ph in found_placeholders:
            if ph not in self.allowed_placeholders:
                errors.append(ErrorCodes.E_COPY_BAD_PLACEHOLDER)
                
        # Channel Requirements -> e.g. ["discount_pct", "product_id"] -> mapped to placeholders theoretically
        # For this MVP, we just check if required strings exist
        reqs = self.channel_rules.get(channel, {}).get("required_elements", [])
        for req_element in reqs:
            # Simple check if the concept is mentioned via placeholder
            req_map = {
                "discount_pct": "{DISCOUNT_TEXT}",
                "product_id": "{PRODUCT}",
                "cta_link": "{CTA}"
            }
            if req_element in req_map and req_map[req_element] not in body_text:
                errors.append(ErrorCodes.E_COPY_CHANNEL_REQUIREMENT_FAIL)

        return list(set(errors)) # return unique errors
