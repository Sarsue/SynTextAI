SyntextAI knowledge assistant

1. QA
answers with source from documents and or web pages
with references included


2. Key Concepts (Flashcards and Key Concepts)
as we extract data we create detailed explanations to help understand the content using web if we must to learn 

give user choice to edit and enhance notes
- page - pdf
- time stamps for videos

3. Evaluations (Quiz)
options for multi-choice or type response (easy | hard mode)
as we extract data we Generate questions to test the students knowledge of the material based on the option they selected present question with choice or no choices
save evaluations for historical purposes.


Testing
Evals for the Above

Domain and Sample Data
finance 
Warren Buffet Newsletter

technical
AI papers e.g Attention is All you need (good domain for LLM to be evaluated)

Deployment
github actions for master branch to deploy the changes to current production environment.

BUILD docker build --no-cache -t syntextaiapp .

RUN docker run --rm -p 3000:3000 --env-file .env --memory 2g syntextaiapp

TAG docker tag syntextaiapp:latest osasdeeon/syntextai:latest

PUSH docker push osasdeeon/syntextai:latest

scp docker-compose.yml deploy.sh .env root@178.128.236.126:/home/root/

log in
ssh root@178.128.236.126

4 Deploy script on server
chmod +x deploy.sh 
./deploy.sh

Current 
Local
# Build and start both services from your local code
colima stop && colima start && docker-compose -f docker-compose.local.yml build && docker-compose -f docker-compose.local.yml up      

Production uses Github actions on master branch 


Marketing
$15 / month for professionals and students
$135 / year 
 