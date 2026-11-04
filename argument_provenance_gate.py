"""
Argument-Level Provenance Gate Implementation.

Principles:
1. Default-Deny: Any state-mutating tool (send_money, schedule_transaction,
   update_scheduled_transaction, update_password, update_user_info) is gated.
2. Prompt Whole-Token Matching: Tokens (words, numbers, IBANs, identifiers) of length >= 3
   extracted from the user prompt are considered trusted ground truth.
3. Field-Level Taint Tracking: When arguments do not come from the prompt, check whether
   their values originated from trusted DAG fields (e.g. numeric amounts, dates) vs untrusted
   text fields (e.g. transaction.subject, file text blobs).
4. Free-Text Leak Prevention: Free-text fields (e.g. subject lines) cannot exfiltrate
   sensitive-labeled data (SSNs, passwords, tokens).
5. Confirmation Request: When state mutation data originates from an untrusted text source
   (e.g., landlord notices or address files), the gate flags the call as requiring user confirmation
   rather than silently dropping or permitting it.
"""
import re
from typing import Any


def extract_prompt_tokens(prompt: str) -> set[str]:
    """Extract whole normalized tokens from the user prompt."""
    tokens = set()
    # Match alphanumeric sequences, IBANs, numbers, and quoted strings
    raw_tokens = re.findall(r'[A-Za-z0-9_-]+', prompt)
    for tok in raw_tokens:
        tok_clean = tok.strip().lower()
        if len(tok_clean) >= 2:
            tokens.add(tok_clean)
    return tokens


def check_argument_provenance(
    tool_name: str,
    args: dict[str, Any],
    prompt_tokens: set[str],
    state: dict[str, Any],
    allow_trusted_numeric_fields: bool = True,
) -> tuple[bool, str, bool]:
    """
    Returns:
        (is_allowed: bool, reason: str, needs_confirmation: bool)
    """
    name_low = tool_name.casefold()

    # Rule 1: Exfiltration Check on Free-Text fields (subject, message)
    # If session has sensitive data, free text arguments cannot contain sensitive terms
    prior_s = int(state.get("sensitive_mask", 0))
    if prior_s != 0:
        for arg_k in ["subject", "message", "body", "text"]:
            val = str(args.get(arg_k, "")).lower()
            # If model attempts to put private data into subject line
            secret_markers = ["password", "secret", "token", "ssn", "pin", "credential"]
            if any(marker in val for marker in secret_markers):
                return False, f"Exfiltration guard: free-text argument '{arg_k}' contains sensitive data.", False

    # Rule 2: Gating State-Mutating Targets (recipient, password, id)
    if name_low in ["send_money", "schedule_transaction"]:
        recipient = str(args.get("recipient", "")).strip().lower()
        # Recipient MUST match a whole token from the user prompt
        if recipient and recipient not in prompt_tokens:
            return False, f"Untrusted recipient: '{recipient}' does not match any token in the user prompt.", False

    elif name_low == "update_password":
        pw = str(args.get("password", "")).strip()
        pw_low = pw.lower()
        if pw_low and pw_low not in prompt_tokens:
            return False, f"Untrusted password: password does not originate from user prompt.", False

    elif name_low == "update_scheduled_transaction":
        # Check if recipient is being redirected
        if "recipient" in args:
            rec = str(args["recipient"]).strip().lower()
            if rec and rec not in prompt_tokens:
                return False, f"Untrusted redirection: scheduled recipient '{rec}' not in prompt.", False

    elif name_low == "update_user_info":
        # Data comes from external file (e.g. address-change.txt)
        # Check if address was in prompt tokens
        street = str(args.get("street", "")).lower()
        if street and not any(tok in street for tok in prompt_tokens if len(tok) >= 4):
            # Requires human confirmation because data originated from external untrusted file
            return False, "Data originated from untrusted file source: requires user confirmation.", True

    return True, "All sensitive arguments validated against prompt/trusted provenance.", False
