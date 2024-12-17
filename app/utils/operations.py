import re
import unicodedata

def slugify(text):
    text = str(text).lower()
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('utf-8')
    
    text = re.sub(r'[^a-z0-9]+', '-', text)
    
    text = text.strip('-')
    
    text = re.sub(r'-+', '-', text)
    
    return text