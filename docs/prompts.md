# Prompt templates and guardrails

These are the prompt templates the answer service uses. The mock
client in the answer service fills them from the retrieved snippets
only. A real language model plugged into the same interface would use
these exact templates, so the prompts are shared and reviewed instead
of living inside one person's head.

## Answer prompt — short style

> Answer the question using only the snippets below. Use the first
> sentence of the first snippet as the direct answer. Name the source
> documents. If the snippets do not cover the question, say you do not
> have the information.

## Answer prompt — detailed style

> Answer the question using only the snippets below. Give the first
> two sentences of the first snippet as the direct answer, then list
> every source document used. Name the source documents. If the
> snippets do not cover the question, say you do not have the
> information.

## Retrieval prompt (how the question is turned into a search)

The retrieval service does not use a model. It takes the question,
drops common words (a, the, what, when, how, and so on), and scores
each paragraph in the local docs by how many question words it shares.
The top 3 paragraphs are returned, with the document name for each.

## Guardrails (apply to every answer, whichever style is on)

1. Only use the retrieved snippets. Never add a fact that is not in
   them.
2. Name the source documents in the answer, so a human can check it.
3. If no snippet covers the question, say so plainly. Never guess.
4. No real member data, no sensitive data, and no personal data is
   ever sent to a model. The docs are synthetic examples only, and
   the mock client sends nothing anywhere.
5. Every answer is logged with its question, snippets, sources, and
   trace id, so it can be checked later.

## Language-model interface

The answer service has a small interface called `LLMClient`, with one
method: `draft(question, snippets, style)`. The default client is
`MockLLMClient`, a local rule-based client that follows the templates
above. `RealLLMClient` is a documented stub: it refuses to run until
a real model endpoint and API key are configured, and even then the
same validation, human check, and logging around it still apply.
