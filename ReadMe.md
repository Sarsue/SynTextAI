Build docker build --no-cache -t syntextaiapp .

RUN docker run --rm -p 3000:3000 --env-file .env syntextaiapp

TAG docker tag syntextaiapp:latest osasdeeon/syntextai:latest

PUSH docker push osasdeeon/syntextai:latest

APP PLATFORM CONNECTS TO DOCKER HUB AND DEPLOYS PUSHED IMAGE

DEPLOY DROPLET 

copy .env-prod, docker-compose-prod.yml and deploy.sh files

scp deploy.sh docker-compose-prod.yml root@147.182.150.68:/root/ 
scp /Users/osas/Documents/dev/docsynth/deploy.sh root@146.190.246.13:/root/

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



scp deploy.sh .env root@138.197.131.87:/home/root/


<!-- # mount volume
docker run --rm -p 3000:3000 --env-file .env \
  -v /app/db:/app/db \
  -v $(pwd)/litestream.yml:/etc/litestream.yml \
  syntextaiapp

# dont mount volume
docker run --rm -p 3000:3000 --env-file .env \
  -v $(pwd)/litestream.yml:/etc/litestream.yml \
  syntextaiapp -->

/Users/osas/Documents/dev/app

docker run --rm \
  -p 3000:3000 \
  --env-file .env \
  -v docsynth_db:/app/db \
  syntextaiapp
