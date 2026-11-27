"""Bounded query preferences; preferences do not establish source credibility."""
def prefer(queries,subject):
    from app.services.work_priority import research_lock
    hints=research_lock.collection_context().get('hints')or{}
    if not hints:return queries
    domains=hints.get('domains',[])[:2];questions=hints.get('questions',[])[:2]
    window=queries[0][2]if queries else'month';extra=[]
    labels=' '.join(hints.get('regions',[])[:2]+hints.get('entities',[])[:2])[:160]
    for domain in domains:extra.append((('site:'+domain+' '+subject+' '+(questions[0]if questions else'')+' '+labels)[:600],None,window))
    for question in questions:extra.append(((subject+' '+question+' '+labels)[:600],None,window))
    return list(dict.fromkeys(extra+list(queries)))[:12]
