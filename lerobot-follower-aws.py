from machine import Pin, I2C, UART
import ssd1306
import network
import ujson
from umqtt.simple import MQTTClient
import time
import config_wifi

AWS_ENV = 'XIAOLerobotArmFollowerAWS'

module_path = "config." + AWS_ENV + ".config_aws"
config_aws = __import__(module_path)

components = module_path.split('.')
for component in components[1:]:
    config_aws = getattr(config_aws, component)

WIFI_NAME = config_wifi.WIFI_NAME
WIFI_PASSWORD = config_wifi.WIFI_PASSWORD

SHOW_DEBUG = False

# Initialize I2C and display
i2c = I2C(scl=Pin(7), sda=Pin(6), freq=100000)
display = None
try:
    display = ssd1306.SSD1306_I2C(128, 64, i2c)
except OSError as e:
    print("Cannot find Display:", e)

class FeetechSCS3215:
    def __init__(self, uart_id=1, baudrate=1000000):
        self.uart = UART(uart_id, baudrate=baudrate, tx=Pin(21), rx=Pin(20))
        self.resolution = 4096
        
    def move_servo(self, servo_id, angle):
        position = int(2048 + (angle * 11.38))
        position = max(0, min(4095, position))
        
        # Pre-calculated packet structure for speed
        packet = [0xFF, 0xFF, servo_id, 5, 0x03, 42, 
                 position & 0xFF, (position >> 8) & 0xFF]
        
        checksum = (~(servo_id + 5 + 0x03 + 42 + (position & 0xFF) + ((position >> 8) & 0xFF))) & 0xFF
        packet.append(checksum)
        
        self.uart.write(bytes(packet))  # No timing overhead for max speed

def print_message(message, sleep_after_message=False):
    message_list = split_string_by_length(str(message), 16)
    
    if display is not None:
        display.fill(0)
        for i in range(len(message_list)):
            display.text(message_list[i].strip(), 0, i * 10, 1)
        display.show()

def split_string_by_length(message, length):
    return [message[i:i+length] for i in range(0, len(message), length)]

with open('/config/' + AWS_ENV + '/key.der', 'rb') as f:
    DEV_KEY = f.read()
with open('/config/' + AWS_ENV + '/cert.der', 'rb') as f:
    DEV_CRT = f.read()

def wifi_connect():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_NAME, WIFI_PASSWORD)
    while wlan.isconnected() == False:
        time.sleep(0.1)

last_positions = {}
last_update_time = 0
msg_count = 0
processed_count = 0

def mqtt_subscribe_callback(topic, msg):
    global last_positions, last_update_time, msg_count, processed_count
    
    msg_count += 1
    
    try:
        current_time = time.ticks_ms()
        
        # Throttle updates to max 200Hz (5ms) for minimal lag
        if time.ticks_diff(current_time, last_update_time) < 5:
            return
            
        data = ujson.loads(msg)
        processed_count += 1
        
        moves_made = 0
        # Extract servo angles and move only if changed
        for key, value in data.items():
            if key.startswith("servo_") and "_" in key:
                parts = key.split("_")
                if len(parts) >= 2:
                    servo_id = int(parts[1])
                    angle = float(value)
                    
                    # Only move if position changed (>0.1 degree for minimal lag)
                    if servo_id not in last_positions or abs(last_positions[servo_id] - angle) > 0.1:
                        servo_controller.move_servo(servo_id, angle)
                        last_positions[servo_id] = angle
                        moves_made += 1
        

        
        last_update_time = current_time
                    
    except Exception as e:
        pass

wifi_connect()
servo_controller = FeetechSCS3215()

try:
    mqtt = MQTTClient(
        client_id=config_aws.THING_NAME,
        server=config_aws.AWS_ENDPOINT,
        port=8883,
        keepalive=60,
        ssl=True,
        ssl_params={'key':DEV_KEY, 'cert':DEV_CRT, 'server_hostname': config_aws.AWS_ENDPOINT})
    
except Exception as e:
    pass

try:
    mqtt.connect()
    mqtt.set_callback(mqtt_subscribe_callback)
    mqtt.subscribe(config_aws.SUB_TOPIC)
    
    last_ping_time = 0
    
    while True:
        try:
            mqtt.check_msg()
        except Exception as e:
            pass
        
        # Ping every 30 seconds to prevent timeout
        current_time = time.ticks_ms()
        if time.ticks_diff(current_time, last_ping_time) > 30000:
            try:
                mqtt.ping()
                last_ping_time = current_time
            except:
                pass
    
except Exception as e:
    pass