#!/bin/bash
# run_measure.sh <armenv> <label> <start> <end> <cache> [extra KEY=VAL ...]
SP=/tmp/claude-0/-home-user-crypto-trading-bot/b53b3a36-e126-5de6-b27f-ca558d8408f8/scratchpad; S=$SP/e17/stat
arm=$1; lab=$2; st=$3; en=$4; cache=$5; shift 5
base=$(grep -E '^SCALPER_' $S/env_$arm.txt); for kv in "$@"; do base=$(printf '%s\n' "$base" | grep -v "^${kv%%=*}="); done
mkdir -p $S/mlog_$lab
cd $S && env $(printf '%s\n' "$base" | xargs) "$@" $(cat $S/dummy.env | xargs) TRADINGBOT_LOG_DIR=$S/mlog_$lab PYTHONPATH=/home/user/crypto-trading-bot /home/user/crypto-trading-bot/.venv/bin/python $S/measure.py $lab $st $en $cache $S/m_$lab.json
