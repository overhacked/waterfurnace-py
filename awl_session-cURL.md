# Login

`curl 'https://symphony.mywaterfurnace.com/account/login' -H 'Connection: keep-alive' -H 'Cache-Control: max-age=0' -H 'Origin: https://symphony.mywaterfurnace.com' -H 'Upgrade-Insecure-Requests: 1' -H 'Content-Type: application/x-www-form-urlencoded' -H 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/69.0.3497.81 Safari/537.36' -H 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8' -H 'Referer: https://symphony.mywaterfurnace.com/account/login?op=logout' -H 'Accept-Encoding: gzip, deflate, br' -H 'Accept-Language: en-US,en;q=0.9' -H 'Cookie: legal-acknowledge=yes; gwid=001EC02B2D8E' --data 'op=login&redirect=%2F&emailaddress=USERNAME&password=SECRET' --compressed`

* Returns cookie `sessionid`
* gwid does not need to be set in advance; it simply selects the active thermostat
* `legal-acknowledge=yes` does need to be set to bypass the warning screen

# WS Request

`curl 'https://awlclientproxy.mywaterfurnace.com/' -H 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10.14; rv:72.0) Gecko/20100101 Firefox/72.0' -H 'Accept: */*' -H 'Accept-Language: en-US,en;q=0.5' --compressed -H 'Sec-WebSocket-Version: 13' -H 'Origin: https://symphony.mywaterfurnace.com' -H 'Sec-WebSocket-Extensions: permessage-deflate' -H 'Sec-WebSocket-Key: y3qTrfH+JNLOc2whV/a7iA==' -H 'DNT: 1' -H 'Connection: keep-alive, Upgrade' -H 'Pragma: no-cache' -H 'Cache-Control: no-cache' -H 'Upgrade: websocket'`

# Logout

`curl 'https://symphony.mywaterfurnace.com/account/login?op=logout' -H 'Connection: keep-alive' -H 'Upgrade-Insecure-Requests: 1' -H 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/69.0.3497.81 Safari/537.36' -H 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8' -H 'Referer: https://symphony.mywaterfurnace.com/' -H 'Accept-Encoding: gzip, deflate, br' -H 'Accept-Language: en-US,en;q=0.9' -H 'Cookie: legal-acknowledge=yes; gwid=001EC02B2D8E; sessionid=ALPHANUM_STRING' --compressed`
