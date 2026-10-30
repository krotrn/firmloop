{#-
Variables (all required; use none when not applicable):
  spec_json: str            the confirmed specification as JSON (summary, rules, scenarios)
  base_code: str | None     the code currently on the device; set for a follow-up
  previous_code: str | None the rejected attempt that the feedback refers to
  feedback: str | None      what was wrong with the previous attempt
All text that is not fixed wording goes through the `untrusted` filter inside its tag.
-#}
The confirmed specification:

<spec>
{{ spec_json | untrusted }}
</spec>
{% if base_code is not none %}

This is the code the device runs now:

<base_code>
{{ base_code | untrusted }}
</base_code>

Modify it so the device behaves as the specification says. Keep the behaviour of every rule and scenario that did not change, and return the complete new file.
{% else %}

Write the complete file for this specification.
{% endif %}
{% if previous_code is not none %}

This was your previous attempt:

<previous_code>
{{ previous_code | untrusted }}
</previous_code>
{% endif %}
{% if feedback is not none %}

Your previous attempt was rejected. Feedback:

<feedback>
{{ feedback | untrusted }}
</feedback>

Fix these problems and return the complete corrected file.
{% endif %}
