docker compose down gluetun qbittorrent sonarr radarr prowlarr
echo "Stopping docker instance:"  
docker stop portainer
docker stop portainer_agent
echo " Active Docker Containers: " 
echo " ------------------------- "
docker container ls
