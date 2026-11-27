"""Concise editorial labels; never replace or expand the grounded reporting."""
import re

TOPICS = (
    (r"landmines?|anti-personnel mines|explosive remnants", "Landmines and explosive remnants"),
    (r"explosive weapons", "Explosive weapons and civilian protection"),
    (r"ransomware", "Ransomware reporting"),
    (r"cyber|hacker|data breach", "Cybersecurity reporting"),
    (r"electricity|power (?:plant|grid)|gas infrastructure", "Energy infrastructure reporting"),
    (r"infectious|outbreak|ebola", "Disease outbreak reporting"),
    (r"pandemic|public health", "Public health reporting"),
    (r"surgical|surgery", "Wartime surgical care"),
    (r"hospital|healthcare|health care|medical", "Healthcare reporting"),
    (r"earthquake|flood|hurricane|disaster", "Natural disaster reporting"),
    (r"humanitarian|displacement|refugee", "Humanitarian conditions"),
    (r"unauthorised access|data tampering|information technology act", "Cybersecurity policy"),
)

def note_headline(text, location=None):
    """Topic label from explicit vocabulary, with only a grounded location added."""
    text=re.sub(r"\[\d+\]", "", str(text or ""))
    label=next((label for pattern,label in TOPICS if re.search(pattern,text,re.I)),None)
    if label is None:
        # A visibly incomplete source fragment, not an invented event assertion.
        text=re.sub(r"^(?:(?:Undated reporting|Partially dated reporting|Source reporting)\s*:\s*)+", "", text, flags=re.I)
        text=re.sub(r"^Source reporting;[^.]*\.\s*", "", text,flags=re.I)
        words=text.split();label="Reporting: "+" ".join(words[:7]).rstrip('.,;:')+("…" if len(words)>7 else "") if words else "Saved reporting"
    location=re.sub(r"\[\d+\]", "", str(location or "")).strip()
    if location and location.casefold() not in {"unknown","not established","none"}:
        label+=" | "+(location if len(location)<=65 else location[:62].rsplit(' ',1)[0]+"…")
    return label
