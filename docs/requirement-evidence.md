# Requirement evidence

Operator profiles can name requirements and map them to configured case IDs. This makes the reason for a test visible: each requirement shows baseline and candidate outcomes, plus the exact checks behind them.

```json
{
  "requirements": [
    {
      "id": "refresh-after-session-expiry",
      "description": "An unexpired refresh token remains valid after its session expires.",
      "cases": [
        { "suite": "visible", "case_id": "reported-example" },
        { "suite": "hidden", "case_id": "different-expiry" }
      ]
    },
    {
      "id": "revocation",
      "description": "Revoked refresh tokens must be rejected.",
      "cases": []
    }
  ]
}
```

Add this field to a complete server-owned evaluation profile, not a candidate repository. References must identify existing cases in that profile; duplicate requirement IDs and duplicate references within a requirement are rejected. The job freezes the profile, including its mappings, when it is queued. Existing profiles without mappings continue to work.

## Outcomes

- **Mapped examples pass:** every mapped case produced a valid passing result.
- **Contradicted by checks:** at least one mapped case failed.
- **Evidence incomplete:** a mapped case is missing, errored, skipped, or came from an invalid/incomparable execution.
- **No checks mapped:** the requirement is declared but has no evidence.

The dashboard shows both revisions and individual case transitions. Exports retain the same evidence. Cases without requirement mappings are counted separately.

This is explicit traceability, not automatic interpretation of issue text. The operator defines the contract. CodeHound does not establish that these requirements completely describe the submitted issue, and passing examples do not prove a general requirement. The overall requirement-adherence check therefore remains **unknown** when all mapped examples pass; a contradiction is reported as a failure of the operator-defined contract. Full task completion remains unverified.

HTTP profile discovery exposes requirement descriptions and mapped-case counts, never the expected values or case inputs. The bundled URL profile defines three public dogfooding requirements; it does not cover the entire CodeHound project.
