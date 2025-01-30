1 BUILD IMAGE

Build docker build --no-cache -t syntextaiapp .

RUN docker run --rm -p 3000:3000 --env-file .env --memory 2g syntextaiapp

TAG docker tag syntextaiapp:latest osasdeeon/syntextai:latest

PUSH docker push osasdeeon/syntextai:latest

APP PLATFORM CONNECTS TO DOCKER HUB AND DEPLOYS PUSHED IMAGE

2 DEPLOY DROPLET 

3 Copy env file and deploy scripts

copy .env.prod, docker-compose-prod.yml and deploy.sh files

scp deploy.sh docker-compose-prod.yml root@147.182.150.68:/root/ 
scp /Users/osas/Documents/dev/docsynth/deploy.sh root@146.190.246.13:/root/
scp  /Users/osas/Documents/dev/docsynth/deploy.sh  /Users/osas/Documents/dev/docsynth/.env.prod root@178.128.236.126:/home/root/

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


