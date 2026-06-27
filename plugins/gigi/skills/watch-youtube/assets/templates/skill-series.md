---
name: { { suggested_skill_name } }
description: Comprehensive guide covering {{video_count}} videos about {{series_title}}
---

# {{series_title}}

{{combined_summary}}

## Prerequisites

{{#each all_prerequisites}}

- {{this}}
  {{/each}}

## Tools & Technologies

{{#each all_tools}}

- {{this}}
  {{/each}}

---

{{#each videos}}

## Part {{@index}}: {{title}}

{{summary}}

### Steps

{{#each steps}}

#### {{@index}}. {{title}}

{{description}}

{{#if commands}}

```bash
{{#each commands}}
{{this}}
{{/each}}
```

{{/if}}

{{#if expected_outcome}}
**Expected:** {{expected_outcome}}
{{/if}}

{{/each}}

### Key Takeaways

{{#each key_concepts}}

- **{{name}}**: {{explanation}}
  {{/each}}

---

{{/each}}

## Common Pitfalls

{{#each all_warnings}}

- {{this}}
  {{/each}}

## Pro Tips

{{#each all_tips}}

- {{this}}
  {{/each}}

---

_Generated from {{video_count}} YouTube videos by watch-youtube skill_
