cd /home/jnicolas/windrose-dedicated-server-docker/


curl -H "Content-Type: application/json" \
  -X POST \
  -d '{"content":"Windrose Server will restart in 60 seconds."}' \
  "https://discord.com/api/webhooks/1495484390711758920/jhynCn6RoGfjn1OU1adKl8FYPadEqZHzx1sG7hpUgAc_d6utjIhaiZFGP1ShGYnGI-eX"


echo "Waiting 60 seconds."
sleep 60
wait
echo "Docker windrose going down."
docker compose down
sleep 15
echo "Restarting windrose Server."
docker compose up -d

