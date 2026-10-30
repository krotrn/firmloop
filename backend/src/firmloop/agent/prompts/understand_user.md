{#-
Variables (all required; use none or an empty list when not applicable):
  followup: bool                    true when base_spec_json is set (derived by the caller)
  request_text: str                 the user's request
  base_spec_json: str | None        current specification as JSON; the base of a follow-up
  answers: list[Answer]             clarifying questions already answered, oldest first; empty if
                                    none. Answer has `question: str` and `answer: str`
  rejected_spec_json: str | None    the specification the user rejected, as JSON
  rejection_feedback: str | None    why the user rejected it
All user text goes through the `untrusted` filter inside its tag.
-#}
{% if followup -%}
The device currently behaves as described by this specification:

<current_spec>
{{ base_spec_json | untrusted }}
</current_spec>

The user asks for this change:

<request>
{{ request_text | untrusted }}
</request>

Return the complete new specification. Keep the ids of everything that does not change, reuse the id of an item you modify, give new items new ids, and list the ids of deleted items in `removed_ids`.
{%- else -%}
The user's request:

<request>
{{ request_text | untrusted }}
</request>
{%- endif %}
{% if answers %}

Questions you asked and the user's answers, oldest first:
{% for a in answers %}
Question: {{ a.question | untrusted }}
<answer>
{{ a.answer | untrusted }}
</answer>
{% endfor %}
Use the answers. Do not ask the same question again.
{% endif %}
{% if rejection_feedback or rejected_spec_json %}

The user rejected the specification you proposed{% if rejected_spec_json %} (in <rejected_spec>){% endif %}.{% if rejection_feedback %} Feedback:

<feedback>
{{ rejection_feedback | untrusted }}
</feedback>
{% endif %}
{% if rejected_spec_json %}

<rejected_spec>
{{ rejected_spec_json | untrusted }}
</rejected_spec>
{% endif %}

Return a corrected specification that addresses the feedback.{% if followup %} It still builds on <current_spec>, not on <rejected_spec>.{% endif %}
{% endif %}
