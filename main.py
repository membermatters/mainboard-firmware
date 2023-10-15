import os
import uwiegand
import network
import config
import ulogging
from urdm6300.urdm6300 import Rdm6300
import time
from machine import Pin, PWM
import ubinascii
import json
import uwebsockets.client
import usocket
from machine import WDT
wdt = WDT(timeout=config.UNLOCK_DELAY * 1000 + 2000)
wdt.feed()
import uselect
import neopixel

ulogging.basicConfig(level=ulogging.INFO)
logger = ulogging.getLogger("main")

CATCH_ALL_EXCEPTIONS = True
STATE = {
    "locked_out": False
}

# setup outputs
buzzer = PWM(Pin(config.BUZZER_PIN), freq=400, duty=0)
lock_pin = Pin(config.LOCK_PIN, Pin.OUT, value=config.LOCK_REVERSED)
if config.USE_NEOPIXEL is True:
    led = neopixel.NeoPixel(Pin(config.LED_PIN), 1)
else:
    led = Pin(config.LED_PIN, Pin.OUT)


def file_or_dir_exists(filename):
    try:
        os.stat(filename)
        return True
    except OSError:
        return False


def save_state(state):
    global STATE
    logger.info("Saving STATE!!")

    STATE = state  # update the in memory state

    try:
        # save the state to flash
        with open('state.json', "w") as state_file:
            json.dump(state, state_file)
            logger.debug("saved state to flash")
        return True

    except Exception as e:
        logger.error("Saving STATE FAILED! Exception:")
        logger.error(str(e))
        return False


def get_state():
    global STATE
    try:
        # create it if it doesn't exist
        if not file_or_dir_exists("state.json"):
            with open('state.json', "w") as state_file:
                json.dump(STATE, state_file)

        with open('state.json') as state_file:
            STATE = json.load(state_file)
            if STATE:
                logger.info("Loaded saved STATE from flash.")
            return STATE

    except Exception as e:
        logger.error("Could not load saved STATE (unhandled error)")
        logger.error(str(e))
        return False

def neopixel_state(state = None):
    # Update the neopixel colour based on the 
    # state of the platform
    logger.info(f"Setting LED to {state}")
    if state is not None:
        if state == "Off":
            led[0] = (0,0,0)
        elif state == "Connected":
            led[0] = (0,0,255)
        elif state == "Disconnected":
            led[0] = (255,0,255)
        elif state == "Scanned":
            led[0] = (0,255,255)
        elif state == "Authorised":
            led[0] = (0,255,0)
        elif state == "Denied":
            led[0] = (255,0,0)
        led.write()
            

def lock_door():
    if config.LOCK_REVERSED:
        lock_pin.on()
    else:
        lock_pin.off()


def unlock_door():
    if config.LOCK_REVERSED:
        lock_pin.off()
    else:
        lock_pin.on()


def led_on():
    if config.LED_REVERSED:
        led.off()
    else:
        led.on()


def led_off():
    if config.LED_REVERSED:
        led.on()
    else:
        led.off()


def buzzer_on():
    if config.BUZZER_ENABLE:
        buzzer.duty(0)


def buzzer_off():
    if config.BUZZER_ENABLE:
        buzzer.duty(1023)


def buzz_alert():
    buzzer_on()
    led_on()
    time.sleep(0.2)

    buzzer_off()
    led_off()
    time.sleep(0.2)

    buzzer_on()
    led_on()
    time.sleep(0.2)

    buzzer_off()
    led_off()


def buzz_ok():
    buzzer_on()
    led_on()

    time.sleep(1)

    led_off()
    buzzer_off()


def led_alert():
    led_off()


def led_ok():
    led_on()

    time.sleep(1)

    led_off()


# setup is starting
if not config.USE_NEOPIXEL:
    buzz_alert()
get_state()  # grab the state from the flash

# setup RFID
if config.WIEGAND_ENABLED:
    # setup wiegand reader
    rfid_reader = uwiegand.Wiegand(config.WIEGAND_ZERO, config.WIEGAND_ONE)
else:
    rfid_reader = Rdm6300()


try:
    # create it if it doesn't exist
    if not file_or_dir_exists("tags.json"):
        with open('tags.json', "w") as new_tags:
            json.dump([], new_tags)

        # if we have any saved tags, load them
    with open('tags.json') as tags:
        parsed_tags = json.load(tags)
        if parsed_tags:
            authorised_rfid_tags = parsed_tags
            logger.info("Loaded %s saved tags from flash.",
                        len(authorised_rfid_tags))
except:
    logger.error("Could not load saved tags (unhandled error)")


# setup wifi
wlan = network.WLAN(network.STA_IF)
local_ip = None  # store our local IP address
local_mac = ubinascii.hexlify(wlan.config(
    'mac')).decode()  # store our mac address

wlan.active(True)
wlan.config(dhcp_hostname="MM_Controller_" + local_mac, txpower=8.5)

wlan_connecting_start = time.ticks_ms()
led_toggle_last_update = time.ticks_ms()
led_toggle_last_state = False

if not wlan.isconnected():
    neopixel_state("Disconnected")
    logger.info('connecting to network...')
    wlan.connect(config.WIFI_SSID, config.WIFI_PASS)
    while not wlan.isconnected():
        if time.ticks_diff(time.ticks_ms(), wlan_connecting_start) > 30000:
            logger.warn("Took too long to wait for WiFi!")
            logger.warn(
                "The ESP32 should continue trying to connect in the background.")
            led_off()
            break

        if time.ticks_diff(time.ticks_ms(), led_toggle_last_update) > 250:
            wdt.feed()
            led_toggle_last_update = time.ticks_ms()

            if led_toggle_last_state:
                if config.USE_NEOPIXEL is True:
                    neopixel_state("Off")
                else:
                    led_off()
                led_toggle_last_state = False

            else:
                if config.USE_NEOPIXEL is True:
                    neopixel_state("Disconnected")
                else:
                    led_on()
                led_toggle_last_state = True

    local_ip = wlan.ifconfig()[0]
    logger.info('Local IP: ' + local_ip)
    logger.info('Local MAC: ' + local_mac)


websocket = None
sock = None
led_update = time.ticks_ms()
ten_second_cron_update = time.ticks_ms()
last_pong = None

DEVICE_SERIAL = local_mac


def setup_websocket_connection():
    global websocket, led_ring, last_pong
    try:
        logger.info("setting up websocket")
        websocket = uwebsockets.client.connect(
            config.PORTAL_WS_URL + "/door/" + DEVICE_SERIAL)
        last_pong = time.ticks_ms()

        auth_packet = {"api_secret_key": config.API_SECRET}
        websocket.send(json.dumps(auth_packet))

        ip_packet = {"command": "ip_address", "ip_address": local_ip}
        websocket.send(json.dumps(ip_packet))

        if config.USE_NEOPIXEL is True:
            neopixel_state("Connected")
        else:
            led_ok()


    except Exception as e:
        logger.error("Couldn't connect to websocket!")
        logger.error(str(e))

        if config.USE_NEOPIXEL is True:
            neopixel_state("Disconnected")
        else:
            led_alert()



def setup_http_server():
    global sock

    try:
        # setup our HTTP server
        sock = usocket.socket()

        # s.setsockopt(usocket.SOL_SOCKET, usocket.SO_REUSEADDR, 1)
        sock.bind(usocket.getaddrinfo('0.0.0.0', 80)[0][-1])
        sock.listen(2)
        return True
    except Exception as e:
        logger.error("Got exception when setting up HTTP server")
        logger.error(str(e))
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
            json.dump(authorised_rfid_tags, new_tags)
            logger.debug("saved tags to flash")

        logger.debug("Syncing tags done!")

    except Exception as e:
        logger.error("Syncing tags FAILED! Exception:")
        logger.error(str(e))


def log_rfid(card_id, rejected=False, locked_out=False):
    logger.info("Logging access!!")

    try:
        if rejected:
            websocket.send(json.dumps(
                {"command": "log_access_denied", "card_id": card_id}))
        elif locked_out:
            websocket.send(json.dumps(
                {"command": "log_access_locked_out", "card_id": card_id}))
        else:
            websocket.send(json.dumps(
                {"command": "log_access", "card_id": card_id}))
    except Exception as e:
        logger.warn("Exception when logging access!")
        logger.error(e)
        pass


def swipe_success():
    logger.warn("Unlocking!")
    unlock_door()
    buzz_ok()

    if config.USE_NEOPIXEL is True:
        neopixel_state("Authorised")
    else:
        led_on()
    time.sleep(config.UNLOCK_DELAY)
    if config.USE_NEOPIXEL is True:
        neopixel_state("Connected")
    else:
        led_off()
    lock_door()
    logger.warn("Locking!")


def swipe_denied():
    if config.USE_NEOPIXEL is True:
        neopixel_state("Denied")
    else:
        buzz_alert()


if config.ENABLE_BACKUP_HTTP_SERVER:
    # try to set up the http server
    if not setup_http_server():
        logger.error("FAILED to setup http server on startup :(")
    else:
        poll = uselect.poll()
        poll.register(sock, uselect.POLLIN)
else:
    logger.warning("Backup http server disabled!")

last_rfid_sync = time.ticks_ms()

setup_websocket_connection()

logger.info("Starting main loop...")

while True:
    wdt.feed()
    try:
        # every 15 minutes sync RFID
        if time.ticks_diff(time.ticks_ms(), last_rfid_sync) >= 900000:
            last_rfid_sync = time.ticks_ms()
            websocket.send(json.dumps({"command": "sync"}))

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

                    if data.get("command") == "update_door_locked_out":
                        STATE["locked_out"] = data.get("locked_out")
                        save_state(STATE)

                        led_ok()

                except Exception as e:
                    logger.error("Error parsing JSON websocket packet!")
                    logger.error(str(e))

        if config.ENABLE_BACKUP_HTTP_SERVER:
            # backup http server for manually bumping a door from the local network
            for event in poll.poll(1):
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

        if (card):
            logger.info("got a card: " + str(card))

            buzzer.duty(512)
            time.sleep(0.1)
            buzzer.duty(0)
            if config.USE_NEOPIXEL is True:
                neopixel_state("Scanned")

            if str(card) in authorised_rfid_tags:
                if STATE["locked_out"]:
                    try:
                        log_rfid(card, locked_out=True)
                    except:
                        pass
                    swipe_denied()

                else:
                    # if there's an issue logging the swipe, open the door anyway
                    try:
                        log_rfid(card)
                    except:
                        pass
                    swipe_success()

            else:
                try:
                    websocket.send(json.dumps({"command": "sync"}))
                    log_rfid(card, rejected=True)
                except:
                    pass
                swipe_denied()

            # dedupe card reads; keep looping until we've cleared the buffer
            while True:
                if not rfid_reader.read_card():
                    break

        card = None

    except KeyboardInterrupt as e:
        raise e

    except Exception as e:
        # turn off the LED and buzzer in case they were left on
        led_off()
        buzzer_off()

        if CATCH_ALL_EXCEPTIONS:
            print("excepted :( ")
            print(e)
            continue
        else:
            raise e
