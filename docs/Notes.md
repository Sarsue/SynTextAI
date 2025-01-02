
BUSINESS

COMPANY NAME, DEFINED OFFERING , DEFINED AUDIENCE , PROBLEM COMPANY IS SOLVING
WHAT WE DO
Syntext AI is a conversational search engine providing concise, cited answers to user (knowledge worker) queries by retrieving information from the web and uploaded documents.
Syntext leverages advanced language models (LLMs) to perform tasks via a natural language interface. Users interact with these tools using natural language, making them intuitive and user-friendly. The problem we solve is efficiency of information retrieval and/or processing using contextual understanding to deliver relevant results or outputs.

What's our secret sauce?
Lean Organization of domain experts...

DEFINED MARKET, DEFINED VALUE and Competitors (2)
This is a growing market with free PRODUCTS LIKE Google NotebookLLM and paid offerings PerplexityAI.  it validates the idea, last year Perplexity made ...

DIFFERENTIATOR
What's our differentiator?

CURRENT STATE OF AFFAIRS (PRODUCT, TEAM OR COMPANY)
Currently I am the only builder with AI agents. 

ASK
I need 10K a month for runway 

WHAT I'LL DO WITH THE ASK
to hire marketers and front end designers and pay for Infrastructure.


TECHNICAL


RAG Pipeline for queries on documents and web 
https://github.com/NirDiamant/RAG_Techniques
https://github.com/NirDiamant/RAG_Techniques/blob/main/all_rag_techniques/crag.ipynb

sample web search 
https://github.com/LexiestLeszek/sova_ollama/blob/main/app.py
https://github.com/rtwfroody/gpt-search/blob/master/gpt_search.py


Document

1. Document Extraction high accuracy here is half the battle
PDF extract to text
Video using fastwhisper to convert audio to text.
save chunks and Page / Segment summary 
we will retrieve chunk and page / segment summary when we run RAG 




2. CHUNK TEXT AND STORE IN DB
Postgres as DB store for vectors and app state aka DB and vectorDB would do same with sqlite if we could figure out the litestream docker replication and backup to google cloud storage.
https://www.enterprisedb.com/blog/rag-app-postgres-and-pgvector
https://medium.com/@brechterlaurin/how-to-use-postgresdb-as-your-one-stop-rag-solution-8536ef7d762e




IMPROVEMENTS

PDF to Markdown FOR BETTER CAPTURING OF FIGURES AND TABLES *
Later using a Multi Modal Model (Pixtral / Qwen / GPT Vision), users ingest information from these documents visually 
https://github.com/kkaarrss/pdf-to-markup/blob/main/pdf_reader.py