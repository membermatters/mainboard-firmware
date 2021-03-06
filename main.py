import config
from urdm6300.urdm6300 import Rdm6300
from leds import *
import json
from machine import Pin, PWM
import socket
import usocket
from uselect import select
import network
import time
import urequests
import ubinascii
import uwebsockets.client
import ulogging

ulogging.basicConfig(level=ulogging.INFO)
logger = ulogging.getLogger("main")

# our own modules

BUZZER_PIN = 32  # IO num, not pin num
LOCK_PIN = 13  # IO num, not pin num
BUZZER_ENABLE = True
UNLOCK_DELAY = config.UNLOCK_DELAY  # seconds to remain unlocked

# setup LED ring
led_ring = Leds()
led_ring.set_all(OFF)

# setup RFID
rfid_reader = Rdm6300()

# setup buzzer
buzzer = PWM(Pin(BUZZER_PIN), freq=400, duty=0)


buzzer.duty(512)
buzzer.freq(400)

time.sleep(0.1)

buzzer.duty(0)
buzzer.freq(400)

# setup lock output
lock_pin = Pin(LOCK_PIN, Pin.OUT)


def locked():
    if config.LOCK_REVERSED:
        lock_pin.on()
    else:
        lock_pin.off()


def unlocked():
    if config.LOCK_REVERSED:
        lock_pin.off()
    else:
        lock_pin.on()


# setup wifi
wlan = network.WLAN(network.STA_IF)
wlan.active(True)

authorised_rfid_tags = list()  # hold our in memory cache of authorised tags


try:
    # if we have any saved tags, load them
    with open('tags.json') as tags:
        parsed_tags = json.load(tags)
        if parsed_tags:
            authorised_rfid_tags = parsed_tags
            logger.info("Loaded %s saved tags from flash.",
                        len(authorised_rfid_tags))
except:
    logger.error("Could not load saved tags")

local_ip = None  # store our local IP address
# store our mac address
local_mac = ubinascii.hexlify(wlan.config('mac')).decode()

wlan_connecting_start = time.ticks_ms()

if not wlan.isconnected():
    logger.info('connecting to network...')
    wlan.config(dhcp_hostname="MMController")
    wlan.connect(config.WIFI_SSID, config.WIFI_PASS)
    while not wlan.isconnected():
        led_ring.run_single_wipe(BLUE)
        if time.ticks_diff(time.ticks_ms(), wlan_connecting_start) > 30000:
            logger.warn("Took too long to wait for WiFi!")
            logger.warn("Will continue trying to connect in the background.")
            led_ring.run_single_pulse("red")
            break
        pass

    local_ip = wlan.ifconfig()[0]
    logger.info('Local IP: ' + local_ip)
    logger.info('Local MAC: ' + local_mac)

# set to purple while we're connecting to the websocket
led_ring.set_all(PURPLE)

websocket = None
sock = None
led_update = time.ticks_ms()
ten_second_cron_update = time.ticks_ms()
last_pong = None
time.sleep(1)

DEVICE_ID = config.DEVICE_ID or local_mac
DEVICE_SERIAL = local_mac


def setup_websocket_connection():
    global websocket, led_ring, last_pong
    try:
        websocket = uwebsockets.client.connect(
            config.PORTAL_WS_URL + "/door/" + DEVICE_SERIAL)
        last_pong = time.ticks_ms()

        auth_packet = {"api_secret_key": config.API_SECRET}
        websocket.send(json.dumps(auth_packet))

        ip_packet = {"command": "ip_address", "ip_address": local_ip}
        websocket.send(json.dumps(ip_packet))

        led_ring.run_single_pulse(GREEN, fadein=True)

    except Exception as e:
        logger.error("Couldn't connect to websocket!")
        logger.error(str(e))
        led_ring.run_single_pulse(RED)


def setup_http_server():
    global sock

    try:
        # setup our HTTP server
        sock = usocket.socket()

        # s.setsockopt(usocket.SOL_SOCKET, usocket.SO_REUSEADDR, 1)
        sock.bind(socket.getaddrinfo('0.0.0.0', 80)[0][-1])
        sock.listen(2)
        return True
    except Exception as e:
        logger.error("Got exception when setting up HTTP server")
        logger.error(e)
        return False


def client_response(conn):
    response = """
            {"success": true}
            """

    conn.send('HTTP/1.1 200 OK\n')
    conn.send('Content-Type: application/json\n')
    conn.send('Connection: close\n\n')
    conn.sendall(response)
    conn.close()


def save_tags(new_tags):
    global authorised_rfid_tags
    logger.info("Syncing tags!!")

    try:
        authorised_rfid_tags = new_tags
        logger.info("Got %s tags!", len(authorised_rfid_tags))
        # save the tags to flash
        with open('tags.json', "w") as new_tags:
            parsed_tags = json.dump(authorised_rfid_tags, new_tags)
            logger.debug("saved tags to flash")

        logger.debug("Syncing tags done!")

    except Exception as e:
        logger.error("Syncing tags FAILED! Exception:")
        logger.error("%s", e)


def log_rfid(card_id, rejected=False):
    logger.info("Logging access!!")

    try:
        if rejected:
            websocket.send(json.dumps(
                {"command": "log_access_denied", "card_id": card_id}))
        else:
            websocket.send(json.dumps(
                {"command": "log_access", "card_id": card_id}))
    except Exception as e:
        logger.warn("Exception when logging access!")
        logger.error(e)
        pass


def swipe_success():
    logger.warn("Unlocking!")
    unlocked()
    buzzer.duty(512)
    buzzer.freq(400)
    time.sleep(0.1)
    buzzer.freq(1000)
    time.sleep(0.3)
    buzzer.duty(0)
    buzzer.freq(400)

    led_ring.run_single_pulse(GREEN, fadein=True)
    led_ring.set_all(GREEN)
    time.sleep(UNLOCK_DELAY)
    led_ring.run_single_pulse(GREEN, fadeout=True)
    locked()
    logger.warn("Locking!")


def swipe_denied():
    buzzer.duty(512)
    buzzer.freq(1000)
    time.sleep(0.05)
    buzzer.duty(0)
    time.sleep(0.05)
    buzzer.freq(200)
    buzzer.duty(512)
    time.sleep(0.05)
    buzzer.duty(0)
    led_ring.run_single_pulse(RED, fadein=True)


if config.ENABLE_BACKUP_HTTP_SERVER:
    # try to set up the http server
    if not setup_http_server():
        logger.error("FAILED to setup http server on startup :(")
else:
    logger.warning("Backup http server disabled!")

last_rfid_sync = time.ticks_ms()

setup_websocket_connection()

logger.info("Starting main loop...")
while True:
    try:
        if gc.mem_free() < 102000:
            gc.collect()

        # every 15 minutes sync RFID
        if time.ticks_diff(time.ticks_ms(), last_rfid_sync) >= 900000:
            last_rfid_sync = time.ticks_ms()
            websocket.send(json.dumps({"command": "sync"}))

        # every 10ms update the animation
        if time.ticks_diff(time.ticks_ms(), led_update) >= 10:
            led_update = time.ticks_ms()
            led_ring.update_animation()

        # every 10 seconds
        cron_period = 10000

        if time.ticks_diff(time.ticks_ms(), ten_second_cron_update) > cron_period:
            ten_second_cron_update = time.ticks_ms()

            if websocket and websocket.open:
                logger.debug("sending ping")

                # if we've missed at least 3 consecutive pongs, then reconnect
                if time.ticks_diff(time.ticks_ms(), last_pong) > cron_period * 4:
                    websocket = None
                    logger.info(
                        "Websocket not open (pong timeout), trying to reconnect.")
                    setup_websocket_connection()
                    # skip the rest of this event loop
                    continue

                try:
                    websocket.send(json.dumps({"command": "ping"}))
                except:
                    websocket = None
                    setup_websocket_connection()
                    # skip the rest of this event loop
                    continue

            else:
                logger.info("Websocket not open, trying to reconnect.")
                setup_websocket_connection()

        if websocket and websocket.open:
            data = websocket.recv()
            if data:
                logger.info("Got websocket packet:")
                logger.info(data)

                try:
                    data = json.loads(data)

                    if data.get("authorised") is not None:
                        logger.info(str(data))

                    if data.get("command") == "pong":
                        last_pong = time.ticks_ms()

                    if data.get("command") == "bump":
                        logger.info("bumping!!")
                        swipe_success()

                    if data.get("command") == "sync":
                        save_tags(data.get("tags"))
                        logger.info("Saved tags with hash: " +
                                    data.get("hash"))
                        led_ring.set_all(
                            (0, 0, GAMMA_CORRECTION[50]))
                        time.sleep(0.05)
                        led_ring.set_all(OFF)

                except Exception as e:
                    logger.error("Error parsing JSON websocket packet!")
                    logger.exception(e)

        if config.ENABLE_BACKUP_HTTP_SERVER:
            # backup http server for manually bumping a door from the local network
            r, w, err = select((sock,), (), (), 1)
            if r:
                for readable in r:
                    conn, addr = sock.accept()
                    request = str(conn.recv(2048))
                    logger.info("got http request!")
                    logger.info(request)
                    client_response(conn)
                    if '/bump?secret=' + config.API_SECRET in request:
                        logger.info("got authenticated bump request")
                        swipe_success()
                        break

        # try to read a card
        card = rfid_reader.read_card()

        # if we got a valid card read
        if (card):
            logger.info("got a card: " + str(card))

            buzzer.duty(512)
            time.sleep(0.1)
            buzzer.duty(0)

            if str(card) in authorised_rfid_tags:
                log_rfid(card)
                swipe_success()

            else:
                websocket.send(json.dumps({"command": "sync"}))
                log_rfid(card, rejected=True)
                swipe_denied()

            # dedupe card reads; keep looping until we've cleared the buffer
            while True:
                if not rfid_reader.read_card():
                    break

        card = None
    except KeyboardInterrupt as e:
        raise e

    except Exception as e:
        print(e)
        continue
