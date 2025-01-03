
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
hire marketers and pay influencers for product Ads
hire front end designers and pay for Infrastructure (VM + LLM)


TECHNICAL


RAG Pipeline for queries on documents and web 
https://github.com/NirDiamant/RAG_Techniques
https://github.com/NirDiamant/RAG_Techniques/blob/main/all_rag_techniques/crag.ipynb



Document

1. Document Extraction high accuracy here is half the battle
PDF extract to text
Video using fastwhisper to convert audio to text.
save chunks and Page / Segment summary 





2. CHUNK TEXT AND STORE IN DB
Same DB store for vectors and app state aka  no specialty vectorDB. sqlite supports vectors if we could figure out the litestream docker replication and backup to google cloud storage I'd use it.
https://www.enterprisedb.com/blog/rag-app-postgres-and-pgvector
https://medium.com/@brechterlaurin/how-to-use-postgresdb-as-your-one-stop-rag-solution-8536ef7d762e


We need to retrieve documents  chunk alongside the page / segment summary they belong to  
evaluate document chunk retrieved with query for relevance
if scores are high > 0.7 generate response from document
if scores less than 0.3 generate response from web search
else concatenate document and web search

IMPROVEMENTS

- PDF to Markdown FOR BETTER CAPTURING OF  FIGURES AND TABLES EXTRACTS*
Later using a Multi Modal Model (Pixtral / Qwen / GPT Vision), we ingest information from these type of documents visually let the LLM extract information for us visually
https://github.com/kkaarrss/pdf-to-markup/blob/main/pdf_reader.py

- Web Search Improvements for better answers 

