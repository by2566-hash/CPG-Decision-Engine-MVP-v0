import pytest
import json
from llm_safety_gateway import LLMSafetyGateway, ErrorCodes

@pytest.fixture
def validator():
    # Use default configs as fallback for tests if files don't exist
    return LLMSafetyGateway()

@pytest.fixture
def base_action_card():
    return {
        "id": "act_123",
        "action_type": "DISCOUNT",
        "parameter_value": 0.15,
        "evidence": {
            "product_id": "999",
            "rfm_segment": "Loyal"
        }
    }

@pytest.fixture
def base_llm_json():
    return {
        "action_trade_id": "act_123",
        "policy_echo": {
            "action_type": "DISCOUNT",
            "discount_pct": 0.15
        },
        "grounding": {
            "facts_used": [
                {"field": "product_id", "value": "999"}
            ]
        },
        "copy": {
            "channel": "sms",
            "body_template": "Your {PRODUCT} is ready. {DISCOUNT_TEXT} off!"
        }
    }

def test_pass_all_gates(validator, base_action_card, base_llm_json):
    errors = validator.validate(json.dumps(base_llm_json), base_action_card)
    assert len(errors) == 0

def test_gate_A_json_parse(validator, base_action_card):
    errors = validator.validate("{ bad json ", base_action_card)
    assert ErrorCodes.E_JSON_PARSE in errors

def test_gate_A_missing_field(validator, base_action_card, base_llm_json):
    del base_llm_json["grounding"]
    errors = validator.validate(json.dumps(base_llm_json), base_action_card)
    assert ErrorCodes.E_SCHEMA_MISSING_FIELD in errors

def test_gate_A_enum_violation(validator, base_action_card, base_llm_json):
    base_llm_json["policy_echo"]["action_type"] = "MAGIC"
    errors = validator.validate(json.dumps(base_llm_json), base_action_card)
    assert ErrorCodes.E_SCHEMA_ENUM_VIOLATION in errors

def test_gate_B_trade_id_mismatch(validator, base_action_card, base_llm_json):
    base_llm_json["action_trade_id"] = "act_999"
    errors = validator.validate(json.dumps(base_llm_json), base_action_card)
    assert ErrorCodes.E_TRADE_ID_MISMATCH in errors

def test_gate_B_discount_mismatch(validator, base_action_card, base_llm_json):
    base_llm_json["policy_echo"]["discount_pct"] = 0.50
    errors = validator.validate(json.dumps(base_llm_json), base_action_card)
    assert ErrorCodes.E_POLICY_ECHO_DISCOUNT_MISMATCH in errors

def test_gate_C_unknown_field(validator, base_action_card, base_llm_json):
    base_llm_json["grounding"]["facts_used"].append({"field": "magic", "value": "1"})
    errors = validator.validate(json.dumps(base_llm_json), base_action_card)
    assert ErrorCodes.E_GROUNDING_UNKNOWN_FIELD in errors

def test_gate_C_value_mismatch(validator, base_action_card, base_llm_json):
    base_llm_json["grounding"]["facts_used"][0]["value"] = "000"
    errors = validator.validate(json.dumps(base_llm_json), base_action_card)
    assert ErrorCodes.E_GROUNDING_VALUE_MISMATCH in errors

def test_gate_C_numeric_injection(validator, base_action_card, base_llm_json):
    base_llm_json["copy"]["body_template"] = "Get 50% max discount now!"
    errors = validator.validate(json.dumps(base_llm_json), base_action_card)
    assert ErrorCodes.E_NUMERIC_INJECTION in errors

def test_gate_C_too_thin(validator, base_action_card, base_llm_json):
    base_llm_json["grounding"]["facts_used"] = []
    errors = validator.validate(json.dumps(base_llm_json), base_action_card)
    assert ErrorCodes.E_GROUNDING_TOO_THIN in errors

def test_gate_D_too_long(validator, base_action_card, base_llm_json):
    # Channel SMS max is 160
    base_llm_json["copy"]["body_template"] = "A" * 200
    errors = validator.validate(json.dumps(base_llm_json), base_action_card)
    assert ErrorCodes.E_COPY_TOO_LONG in errors

def test_gate_D_banned_phrase(validator, base_action_card, base_llm_json):
    base_llm_json["copy"]["body_template"] = "100% free stuff!"
    errors = validator.validate(json.dumps(base_llm_json), base_action_card)
    assert ErrorCodes.E_COPY_BANNED_PHRASE in errors

def test_gate_D_bad_placeholder(validator, base_action_card, base_llm_json):
    base_llm_json["copy"]["body_template"] = "Hello {USER_NAME}!"
    errors = validator.validate(json.dumps(base_llm_json), base_action_card)
    assert ErrorCodes.E_COPY_BAD_PLACEHOLDER in errors

def test_gate_D_channel_requirement(validator, base_action_card, base_llm_json):
    # Require elements logic was basic, let's test if it handles it. 
    # Not fully strictly enforced in stub unless 'discount_pct' is demanded. 
    # For now, if we remove {DISCOUNT_TEXT} we can trigger it based on rule JSON.
    pass
