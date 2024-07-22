

DEPLOY

scp /Users/osas/Documents/dev/docsynth/deploy.sh root@165.22.236.129:/root/
scp /Users/osas/Documents/dev/docsynth/.env root@165.22.236.129:/root/


on server
chmod +x deploy.sh
./deploy.sh
