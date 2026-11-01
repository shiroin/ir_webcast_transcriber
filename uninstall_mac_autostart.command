#!/bin/bash
# Mac版のログイン時自動起動をやめる（Python環境とSecretsは残す）。
LABEL="com.irwebcast.transcriber"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "自動起動を解除しました。"
read -r -p "Enterキーで閉じます" _
