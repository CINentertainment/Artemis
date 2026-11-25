"""Explicit broad discovery profiles; matching establishes subject relevance only."""
import re

PROFILES = {
    'recent armed conflict': {
        'key':'conflict', 'query':'armed conflict civilian impact', 'domains':['reliefweb.int','un.org'],
        'pattern':r'\b(?:armed conflict|warfare|invasion|airstrikes?|missile strikes?|hostilities|shelling|ceasefire|fighting|war)\b'},
    'recent natural disasters': {
        'key':'disaster', 'query':'earthquake flood storm disaster impact', 'domains':['reliefweb.int','wmo.int'],
        'pattern':r'\b(?:earthquakes?|flood(?:s|ing)?|hurricanes?|cyclones?|typhoons?|wildfires?|tsunamis?|landslides?|drought|natural disaster)\b'},
    'recent disease outbreaks': {
        'key':'outbreak', 'query':'disease outbreak emergency', 'domains':['who.int','cdc.gov'],
        'pattern':r'\b(?:outbreaks?|epidemic|pandemic|cholera|ebola|mpox|measles|avian influenza)\b'},
    'recent critical infrastructure disruptions': {
        'key':'infrastructure', 'query':'power water transport infrastructure outage disruption', 'domains':['reliefweb.int','iea.org'],
        'pattern':r'\b(?:power|electricity|water|rail|transport|telecommunications?|internet|pipeline|port|airport|grid)\b',
        'also':r'\b(?:outages?|disrupt\w*|blackouts?|damage\w*|shutdown\w*|closed|closure|attack\w*|fail\w*)\b'},
    'recent economic disruptions': {
        'key':'economic', 'query':'economic disruption trade supply chain crisis', 'domains':['imf.org','wto.org'],
        'pattern':r'\b(?:econom\w*|trade|shipping|supply chain|inflation|energy prices|food prices|tariffs?|sanctions?)\b',
        'also':r'\b(?:disrupt\w*|crisis|shortage\w*|surge\w*|declin\w*|recession|tariffs?|sanctions?)\b'},
    'recent cybersecurity incidents': {
        'key':'cybersecurity', 'query':'cyberattack ransomware data breach incident', 'domains':['cisa.gov','cert.europa.eu'],
        'pattern':r'\b(?:cyberattacks?|cyber attacks?|ransomware|data breach\w*|cybersecurity incident\w*|exploited vulnerabilit\w*)\b'},
}


def profile(topic):
    return PROFILES.get(' '.join(str(topic).casefold().strip(' .').split()))


def relevant(topic, excerpt):
    selected=profile(topic)
    if not selected:
        return None
    if not re.search(selected['pattern'],excerpt,re.I):
        return False
    return not selected.get('also') or bool(re.search(selected['also'],excerpt,re.I))
