Local
# Build and start both services from your local code
colima stop && colima start && docker-compose -f docker-compose.local.yml build && docker-compose -f docker-compose.local.yml up      

Production uses Github actions on master branch 