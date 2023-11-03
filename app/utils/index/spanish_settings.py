settings = {
    "index": {
        "analysis": {
            "analyzer": {
                "analyzer_spanish": {
                    "tokenizer": "standard",
                    "filter": [
                        "lowercase",
                        "asciifolding",
                        "default_spanish_stopwords",
                        "default_spanish_stemmer"
                    ]
                }
            },
            "filter": {
                "default_spanish_stemmer": {
                    "type": "stemmer",
                    "name": "spanish"
                },
                "default_spanish_stopwords": {
                    "type": "stop",
                    "stopwords": [
                        "_spanish_"
                    ]
                }
            }
        }
    }
}
