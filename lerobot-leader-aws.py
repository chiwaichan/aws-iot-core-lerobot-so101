from machine import Pin, I2C, UART
import ssd1306
import network
import ujson
from umqtt.simple import MQTTClient
import time
import urandom
import config_wifi

AWS_ENV = 'XIAOLerobotArmLeaderAWS'

module_path = "config." + AWS_ENV + ".config_aws"
config_aws = __import__(module_path)

components = module_path.split('.')
for component in components[1:]:
    config_aws = getattr(config_aws, component)

WIFI_NAME = config_wifi.WIFI_NAME
WIFI_PASSWORD = config_wifi.WIFI_PASSWORD

SHOW_DEBUG = True

# Initialize I2C and display
i2c = I2C(scl=Pin(7), sda=Pin(6), freq=100000)
display = None
try:
    display = ssd1306.SSD1306_I2C(128, 64, i2c)
except OSError as e:
    print("Cannot find Display:", e)

# Servo configurations with their ranges
SERVOS = {
    1: {"min": -180, "max": 180, "name": "shoulder_pan"},
    2: {"min": -90, "max": 90, "name": "shoulder_lift"},
    3: {"min": -135, "max": 135, "name": "elbow_flex"},
    4: {"min": -90, "max": 90, "name": "wrist_flex"},
    5: {"min": -180, "max": 180, "name": "wrist_roll"},
    6: {"min": -90, "max": 90, "name": "gripper"}
}

class FeetechSCS3215:
    def __init__(self, uart_id=1, baudrate=1000000):
        print(f"Initializing UART {uart_id} at {baudrate} baud")
        self.uart = UART(uart_id, baudrate=baudrate, tx=Pin(21), rx=Pin(20))
        print("UART initialized successfully")
        
    def read_position(self, servo_id):
        try:
            while self.uart.any():
                self.uart.read(1)
            
            checksum = (~(servo_id + 4 + 0x02 + 56 + 2)) & 0xFF
            self.uart.write(bytes([0xFF, 0xFF, servo_id, 4, 0x02, 56, 2, checksum]))
            
            time.sleep(0.01)
            
            if self.uart.any():
                response = self.uart.read()
                if len(response) >= 8 and response[0] == 0xFF:
                    pos = response[5] | (response[6] << 8)
                    return int((pos - 2048) / 11.38)
            return None
        except:
            return None
    
    def disable_torque(self, servo_id):
        packet = [0xFF, 0xFF, servo_id, 4, 0x03, 40, 0]
        checksum = (~(servo_id + 4 + 0x03 + 40 + 0)) & 0xFF
        packet.append(checksum)
        
        self.uart.write(bytes(packet))
        time.sleep(0.01)
    
    def disable_all_torque(self):
        print("Disabling torque on all servos...")
        for servo_id in range(1, 7):
            self.disable_torque(servo_id)
        print("All servos can now be moved manually")
    
    def read_all_positions(self):
        positions = {}
        for servo_id in range(1, 7):
            angle = self.read_position(servo_id)
            if angle is not None:
                positions[servo_id] = angle
        return positions

def print_message(message, sleep_after_message=False):
    print(message)
    message_list = split_string_by_length(str(message), 16)
    
    if display is not None:
        display.fill(0)
        for i in range(len(message_list)):
            display.text(message_list[i].strip(), 0, i * 10, 1)
        display.show()
        
    if sleep_after_message:
        time.sleep(2)

def split_string_by_length(message, length):
    return [message[i:i+length] for i in range(0, len(message), length)]

def generate_servo_data_from_positions(positions):
    servo_data = {}
    for servo_id, config in SERVOS.items():
        if servo_id in positions:
            servo_data["servo_" + str(servo_id) + "_" + config["name"]] = positions[servo_id]
        else:
            servo_data["servo_" + str(servo_id) + "_" + config["name"]] = 0
    return servo_data

def reconnect_mqtt():
    global mqtt
    try:
        print_message("Reconnecting MQTT...")
        mqtt.disconnect()
        time.sleep(2)
        mqtt.connect()
        mqtt.set_callback(mqtt_subscribe_callback)
        mqtt.subscribe(config_aws.SUB_TOPIC)
        mqtt.subscribe("$aws/things/" + config_aws.THING_NAME.decode() + "/shadow/update/delta")
        print_message("MQTT reconnected")
        return True
    except Exception as e:
        print_message("Reconnect failed: " + str(e))
        return False

print_message(WIFI_NAME)
print_message("Starting certificate loading...")

with open('/config/' + AWS_ENV + '/key.der', 'rb') as f:
    DEV_KEY = f.read()
with open('/config/' + AWS_ENV + '/cert.der', 'rb') as f:
    DEV_CRT = f.read()

print_message("Certificates loaded successfully")

def wifi_connect():
    if SHOW_DEBUG:
        print_message('Connecting to wifi...')  
    
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_NAME, WIFI_PASSWORD)
    while wlan.isconnected() == False:
        print_message('Waiting for connection...', True)
        time.sleep(1)
    
    if SHOW_DEBUG:
        print_message('Connection: %s' % str(wlan.ifconfig()), True)

def mqtt_subscribe_callback(topic, msg):
    print_message("Received topic: %s message: %s" % (topic, msg), True)

print_message("Starting WiFi connection...")
wifi_connect()
print_message("WiFi connected successfully")

print_message("config_aws.THING_NAME")
print_message(config_aws.THING_NAME)
print_message("Setting up servo controller...")
servo_controller = FeetechSCS3215()
servo_controller.disable_all_torque()
print_message("Servo controller ready")

print_message("Setting up MQTT client...")

try:
    mqtt = MQTTClient(
        client_id=config_aws.THING_NAME,
        server=config_aws.AWS_ENDPOINT,
        port=8883,
        keepalive=60,
        ssl=True,
        ssl_params={'key':DEV_KEY, 'cert':DEV_CRT, 'server_hostname': config_aws.AWS_ENDPOINT})
    
except Exception as e:
    print_message("Failed to connect to MQTT broker: " + str(e))
    
print_message("MQTT client created, attempting connection...")

try:
    print_message("Calling mqtt.connect()...")
    mqtt.connect()
    print_message("MQTT connected successfully")
    mqtt.set_callback(mqtt_subscribe_callback)
    mqtt.subscribe(config_aws.SUB_TOPIC)
    mqtt.subscribe("$aws/things/" + config_aws.THING_NAME.decode() + "/shadow/update/delta")
    print_message("MQTT subscriptions set up")
    
    print_message("Initializing shadow...")
    try:
        shadow_init = ujson.dumps({
            "state": {
                "reported": {
                    "online": True,
                    "device_type": config_aws.THING_NAME.decode()
                }
            }
        })
        mqtt.publish("$aws/things/" + config_aws.THING_NAME.decode() + "/shadow/update", shadow_init)
        print_message("Shadow initialized")
    except Exception as e:
        print_message("Shadow init error: " + str(e))
    
    print_message("Entering main loop...")
    previous_positions = {}
    publish_count = 0
    last_publish_time = 0
    last_ping_time = 0
    
    while True:
        positions = servo_controller.read_all_positions()
        
        angles_changed = False
        current_time = time.ticks_ms()
        
        for servo_id in range(1, 7):
            if servo_id in positions:
                current_angle = positions[servo_id]
                if servo_id not in previous_positions or abs(previous_positions[servo_id] - current_angle) > 1:
                    angles_changed = True
                    previous_positions[servo_id] = current_angle
        
        if angles_changed and time.ticks_diff(current_time, last_publish_time) > 100:
            servo_data = generate_servo_data_from_positions(positions)
            
            message_data = {
                config_aws.PARTITION_KEY.decode(): config_aws.THING_NAME.decode()
            }
            message_data.update(servo_data)
            
            message = ujson.dumps(message_data)
            
            try:
                mqtt.publish(config_aws.PUB_TOPIC, message)
                publish_count += 1
                last_publish_time = current_time
                
                if SHOW_DEBUG and publish_count % 50 == 0:
                    print(f"Published {publish_count}: {len(positions)} servos")
                    
            except Exception as e:
                if e.args[0] == -104:
                    if reconnect_mqtt():
                        continue
                if SHOW_DEBUG:
                    print(f'Publish error: {e}')
        
        try:
            mqtt.check_msg()
        except Exception as e:
            if e.args[0] == -104:
                if reconnect_mqtt():
                    continue
            if SHOW_DEBUG:
                print(f'Check message error: {e}')
        
        if time.ticks_diff(current_time, last_ping_time) > 30000:
            try:
                mqtt.ping()
                last_ping_time = current_time
            except:
                pass
        
        time.sleep(0.01)
    
except Exception as e:
    print_message("MQTT connection error: " + str(e))
    import sys
    sys.print_exception(e)
