cd ~/llm
while ! grep -q SETUP_DONE setup.log; do sleep 20; done
nohup bash ~/llm/serve.sh > ~/llm/serve.log 2>&1 < /dev/null &
echo $! > serve.pid
for i in $(seq 1 120); do curl -sf http://127.0.0.1:8100/v1/models > /dev/null && break; sleep 10; done
curl -sf http://127.0.0.1:8100/v1/models | head -c 200; echo
. venv/bin/activate && python calibrate.py > calibration.log 2>&1
echo PIPELINE_DONE >> calibration.log
