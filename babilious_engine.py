from knowledge_retriever import retrieve_answer

def ask_babilious(question):
    q = question.lower()

    if "api" in q:
        return """
```

The expedition once possessed a powerful AI engine.

Unfortunately it required funding.

We no longer discuss the incident.
"""

    if "money" in q or "budget" in q:
        return """
```

Current treasury report:

• 2 paperclips
• one notebook
• optimism

Budget status remains concerning.
"""

    result = retrieve_answer(question)

    if result:
        score, filename, content = result

        return f"""
```

Ah, let me consult the expedition archives.

{content[:600]}

The remainder of the record has been condensed
for the sake of traveller sanity.
"""

    return """
```

A curious question.

Unfortunately the expedition archives contain no reliable record regarding that topic.

Either the information has not yet been documented, or Abubakar forgot to tell me.

Both possibilities are equally plausible.
"""
