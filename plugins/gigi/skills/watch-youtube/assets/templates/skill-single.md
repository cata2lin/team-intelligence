---
name: { { suggested_skill_name } }
description: { { summary } }
---

# {{title}}

{{summary}}

## Prerequisites

{{#each prerequisites}}

- {{this}}
  {{/each}}

## Steps

{{#each steps}}

### {{@index}}. {{title}}

{{description}}

{{#if commands}}

```bash
{{#each commands}}
{{this}}
{{/each}}
```

{{/if}}

{{#if code}}

```{{language}}
{{code}}
```

{{/if}}

{{#if expected_outcome}}
**Expected:** {{expected_outcome}}
{{/if}}

{{/each}}

## Key Concepts

{{#each key_concepts}}

### {{name}}

{{explanation}}

{{/each}}

{{#if code_snippets}}

## Code Reference

{{#each code_snippets}}

### {{description}}

```{{language}}
{{code}}
```

{{/each}}
{{/if}}

## Tools & Technologies

{{#each tools}}

- {{this}}
  {{/each}}

{{#if warnings}}

## Common Pitfalls

{{#each warnings}}

- **Warning:** {{this}}
  {{/each}}
  {{/if}}

{{#if tips}}

## Pro Tips

{{#each tips}}

- {{this}}
  {{/each}}
  {{/if}}

---

_Generated from YouTube video by watch-youtube skill_
