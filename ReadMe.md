

DEPLOY
copy .env, docker-compose.yml and deploy.sh files

scp deploy.sh docker-compose-prod.yml root@147.182.150.68:/root/
scp /Users/osas/Documents/dev/docsynth/deploy.sh root@146.190.246.13:/root/

on server
chmod +x deploy.sh
./deploy.sh


Troubleshooting
scp may fail cos of key gen
Open the file: nano ~/.ssh/known_hosts
Go to a specific line: Ctrl + _ (underscore), enter the line number, press Enter
Delete the line: Ctrl + K
Save and exit: Ctrl + X, Y, Enter 
