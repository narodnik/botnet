import asyncio
import os
import sys
import json
import traceback

from typing import Optional

from nio import (AsyncClient, ClientConfig, DevicesError, Event,InviteEvent, LoginResponse,
                 LocalProtocolError, MatrixRoom, MatrixUser, RoomMessageText,
                 crypto, exceptions, RoomSendResponse)

STORE_FOLDER = "nio_store/"
SESSION_DETAILS_FILE = "credentials.json"

ROOM_ID = "!BQEjGPeQwWMEvvOLtO:dark.fi"

ALICE_HOMESERVER = "https://halogen.city"

with open("config.json", "r") as f:
    config = json.load(f)
    ALICE_USER_ID = config["user_id"]
    ALICE_PASSWORD = config["password"]

class CustomEncryptedClient(AsyncClient):
    def __init__(self, homeserver, user='', device_id='', store_path='', config=None, ssl=None, proxy=None):
        # Calling super.__init__ means we're running the __init__ method
        # defined in AsyncClient, which this class derives from. That does a
        # bunch of setup for us automatically
        super().__init__(homeserver, user=user, device_id=device_id, store_path=store_path, config=config, ssl=ssl, proxy=proxy)

        # if the store location doesn't exist, we'll make it
        if store_path and not os.path.isdir(store_path):
            os.mkdir(store_path)

        # print all the messages we receive
        self.add_event_callback(self.cb_print_messages, RoomMessageText)

    async def login(self) -> None:
        """Log in either using the global variables or (if possible) using the
        session details file.

        NOTE: This method kinda sucks. Don't use these kinds of global
        variables in your program; it would be much better to pass them
        around instead. They are only used here to minimise the size of the
        example.
        """
        # Restore the previous session if we can
        # See the "restore_login.py" example if you're not sure how this works
        if os.path.exists(SESSION_DETAILS_FILE) and os.path.isfile(SESSION_DETAILS_FILE):
            try:
                with open(SESSION_DETAILS_FILE, "r") as f:
                    config = json.load(f)
                    self.access_token = config['access_token']
                    self.user_id = config['user_id']
                    self.device_id = config['device_id']

                    # This loads our verified/blacklisted devices and our keys
                    self.load_store()
                    print(f"Logged in using stored credentials: {self.user_id} on {self.device_id}")

            except IOError as err:
                print(f"Couldn't load session from file. Logging in. Error: {err}")
            except json.JSONDecodeError:
                print("Couldn't read JSON file; overwriting")

        # We didn't restore a previous session, so we'll log in with a password
        if not self.user_id or not self.access_token or not self.device_id:
            # this calls the login method defined in AsyncClient from nio
            resp = await super().login(ALICE_PASSWORD)

            if isinstance(resp, LoginResponse):
                print("Logged in using a password; saving details to disk")
                self.__write_details_to_disk(resp)
            else:
                print(f"Failed to log in: {resp}")
                sys.exit(1)

    def trust_devices(self, user_id: str) -> None:
        for device_id, olm_device in self.device_store[user_id].items():
            self.verify_device(olm_device)
            print(f"Trusting {device_id} from user {user_id}")

    async def cb_print_messages(self, room: MatrixRoom, event: RoomMessageText):
        if room.display_name != "dev":
            return

        message = event.body.strip()
        if message.startswith("#"):
            print(f"{room.display_name} | {room.user_name(event.sender)}: {event.body}")

        if message.startswith("#topic"):
            topic = message[len("#topic "):]
            print(topic)
            self.add_topic(topic)
            await self.send_message(f"Added topic '{topic}'")
        elif message.startswith("#help"):
            help_text = """Commands:

#topic      - Add a new weekly topic
#list       - List weekly topics
#start      - Start the meeting
#next       - Move onto next topic
#end        - Finish the meeting
#clear      - Clear all remaining topics
#help       - Show this help text"""
            await self.send_message(help_text)
        elif message.startswith("#list"):
            topic_text = ""
            for i, topic in enumerate(self.get_topics(), start=1):
                topic_text += f"{i}. {topic}\n"
            await self.send_message(topic_text)
        elif message.startswith("#start"):
            await self.send_message("Meeting started.")
            await self.next_topic()
        elif message.startswith("#next"):
            await self.next_topic()
        elif message.startswith("#clear"):
            self.write_topics([])
            await self.send_message("Topics cleared.")
        elif message.startswith("#end"):
            await self.send_message("Meeting stopped.")

    async def send_message(self, message):
        try:
            await self.room_send(
                room_id=ROOM_ID,
                message_type="m.room.message",
                content = {
                    "msgtype": "m.text",
                    "body": message
                },
                ignore_unverified_devices=True,
            )
        except Exception:
            print("Exception:" + traceback.format_exc())

    def add_topic(self, topic):
        topics = self.get_topics()
        topics.append(topic)
        self.write_topics(topics)

    def write_topics(self, topics):
        with open("topics.json", "w") as f:
            json.dump(topics, f)

    def get_topics(self):
        try:
            with open("topics.json", "r") as f:
                topics = json.load(f)
        except FileNotFoundError:
            topics = []
        return topics

    async def next_topic(self):
        topics = self.get_topics()
        try:
            current_topic = topics.pop(0)
        except IndexError:
            await self.send_message("No topics")
            return

        await self.send_message(f"Current topic is: {current_topic}")
        self.write_topics(topics)

    @staticmethod
    def __write_details_to_disk(resp: LoginResponse) -> None:
        with open(SESSION_DETAILS_FILE, "w") as f:
            json.dump({
                "access_token": resp.access_token,
                "device_id": resp.device_id,
                "user_id": resp.user_id
            }, f)

    async def do_sync(self):
        while True:
            try:
                return await self.sync_forever(30000, full_state=True)
            except Exception:
                print("Exception:" + traceback.format_exc())


async def run_client(client: CustomEncryptedClient) -> None:
    # This is our own custom login function that looks for a pre-existing config
    # file and, if it exists, logs in using those details. Otherwise it will log
    # in using a password.
    await client.login()

    async def after_first_sync():
        print("Awaiting sync")
        await client.synced.wait()
        #client.trust_devices(ALICE_USER_ID)
        print("Send hello")

        #await client.send_hello_world()

    after_first_sync_task = asyncio.ensure_future(after_first_sync())

    # We use full_state=True here to pull any room invites that occured or
    # messages sent in rooms _before_ this program connected to the
    # Matrix server
    sync_forever_task = asyncio.ensure_future(client.do_sync())

    await asyncio.gather(
        # The order here IS significant! You have to register the task to trust
        # devices FIRST since it awaits the first sync
        after_first_sync_task,
        sync_forever_task
    )

async def main():
    # By setting `store_sync_tokens` to true, we'll save sync tokens to our
    # store every time we sync, thereby preventing reading old, previously read
    # events on each new sync.
    # For more info, check out https://matrix-nio.readthedocs.io/en/latest/nio.html#asyncclient
    config = ClientConfig(store_sync_tokens=True)
    client = CustomEncryptedClient(
        ALICE_HOMESERVER,
        ALICE_USER_ID,
        store_path=STORE_FOLDER,
        config=config,
        ssl=True,
    )

    try:
        await run_client(client)
    except (asyncio.CancelledError, KeyboardInterrupt):
        await client.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

