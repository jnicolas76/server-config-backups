docker compose up -d
echo "Bringing up Docker Container(s):" 
docker start portainer
docker start portainer_agent
#docker logs -f gluetun
