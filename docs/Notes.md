
BUSINESS

IDEAS
- PDF to Markdown FOR BETTER CAPTURING OF  FIGURES AND TABLES EXTRACTS*
Later using a Multi Modal Model (Pixtral / Qwen / GPT Vision), we ingest information from these type of documents visually let a Multimodal Model extract information for us visually
https://github.com/kkaarrss/pdf-to-markup/blob/main/pdf_reader.py

- resolve intent, gather context (chat history, web, documents), generate response


Deploying
1 BUILD IMAGE

Build docker build --no-cache -t syntextaiapp .

RUN docker run --rm -p 3000:3000 --env-file .env --memory 2g syntextaiapp

TAG docker tag syntextaiapp:latest osasdeeon/syntextai:latest

PUSH docker push osasdeeon/syntextai:latest

APP PLATFORM CONNECTS TO DOCKER HUB AND DEPLOYS PUSHED IMAGE

2 DEPLOY DROPLET 

3 Copy env file and deploy scripts

copy .env, docker-compose-prod.yml, settings.yml and deploy.sh files


scp  /Users/osas/Documents/dev/app/deploy.sh  /Users/osas/Documents/dev/app/.env root@178.128.236.126:/home/root/

ssh root@178.128.236.126

4 Deploy script
on server chmod +x deploy.sh
 ./deploy.sh

rename files

Troubleshooting scp may fail cos of key gen 
Open the file: nano ~/.ssh/known_hosts 
Go to a specific line: Ctrl + _ (underscore), 
enter the line number, 
press Enter 
Delete the line: Ctrl + K
Save and exit: Ctrl + X, Y, Enter


how to override searxng settings
docker run -d -p 8888:8080 --name searxng \
  -v $(pwd)/settings.yml:/etc/searxng/settings.yml \
  searxng/searxng:latest
