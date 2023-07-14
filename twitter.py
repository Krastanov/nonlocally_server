from requests_oauthlib import OAuth1Session
import json

class Twitter:
    def __init__(self, keys=None, savekeys=lambda keys : None):
        # Inputs:
        # keys: a dictionary with entries for the needed keys/secrets
        # savekeys: a function callback for saving the keys dictionary after authentication modifies it
        try:
            self.consumer_key = keys["consumer_key"]
            self.consumer_secret = keys["consumer_secret"]
        except:
            print(keys)
            return
            print("Go here: https://developer.twitter.com/en/portal/projects-and-apps and click on the key that says \"keys and tokens\", and get the consumer key and secret.")
            self.consumer_key = input("Paste the Consumer Key here: ")
            self.consumer_secret = input("Paste the Consumer Secret here: ")
            keys = {
                    "consumer_key" : self.consumer_key,
                    "consumer_secret" : self.consumer_secret
                    }
            savekeys(keys)

        # if access_token and access_secret are already present, that's all we need so we can retrieve those and be done.
        if "access_token" in keys and "access_secret" in keys:
            self.access_token = keys["access_token"]
            self.access_secret = keys["access_secret"]
            return
       
        # use the consumer key/secret to retrieve resource owner key/secret
        request_token_url = "https://api.twitter.com/oauth/request_token?oauth_callback=oob&x_auth_access_type=write"
        oauth = OAuth1Session(self.consumer_key, client_secret=self.consumer_secret)
        try:
            fetch_response = oauth.fetch_request_token(request_token_url)
            resource_owner_key = fetch_response.get("oauth_token")
            resource_owner_secret = fetch_response.get("oauth_token_secret")
        except ValueError:
            print(
                "There may have been an issue with the consumer_key or consumer_secret you entered."
            )
            raise

        # use the resource owner key/secret to retrieve the access token/secret
        # Get authorization
        base_authorization_url = "https://api.twitter.com/oauth/authorize"
        authorization_url = oauth.authorization_url(base_authorization_url)
        print("Please go here and authorize: %s" % authorization_url)
        verifier = input("Paste the PIN here: ")

        # Get the access token
        access_token_url = "https://api.twitter.com/oauth/access_token"
        oauth = OAuth1Session(
            self.consumer_key,
            client_secret=self.consumer_secret,
            resource_owner_key=resource_owner_key,
            resource_owner_secret=resource_owner_secret,
            verifier=verifier,
        )
        oauth_tokens = oauth.fetch_access_token(access_token_url)

        self.access_token = oauth_tokens["oauth_token"]
        self.access_secret = oauth_tokens["oauth_token_secret"]
        keys["access_token"] = self.access_token
        keys["access_secret"] = self.access_secret
        savekeys(keys)

    def upload_media(self, media):
        # Input:
        # media: a bytes object containing a png image
        # Output:
        # A media_id string referring to the uploaded image or None if the upload failed

        # according to the documentation this is supposed to be a 3-step process with an "INIT" then one or more "APPEND" then a "FINALIZE".
        # I've found experimentally it doesn't really work that way and talk on github confirms the API does not behave as documented.
        # Regardless, this code seems to work as of 7/14/2023

        request_data = {
                "command" : "FINALIZE",
                "media_type" : "image/png", # TODO support other types if necessary
                "total_bytes" : len(media),
                "media_category" : "tweet_image"
                }
        files = {
                "media":media
                }
        media_id = None
        try:
            oauth = OAuth1Session(
                self.consumer_key,
                client_secret=self.consumer_secret,
                resource_owner_key=self.access_token,
                resource_owner_secret=self.access_secret,
            )

            response = oauth.post('https://upload.twitter.com/1.1/media/upload.json', json=request_data, files=files)
            media_id = None
            if not response.ok:
                print(f"Media upload FAILED: {response.json()}")
                return None
            else:
                return str(response.json()["media_id"])
        except Exception as e:
            print(f"Media upload FAILED: {e}")
            return None


    def tweet(self, text, media_id=None):
        # Input:
        # text: A string containing the body of the tweet.
        # media_id: A string containing a media_id for an image to be attached (e.g. the return value from an upload_media call).  If None then no image will be attached.
        # Output:
        # a boolean True/False indicating if the tweet was made successfully.


        # Make the request
        oauth = OAuth1Session(
            self.consumer_key,
            client_secret=self.consumer_secret,
            resource_owner_key=self.access_token,
            resource_owner_secret=self.access_secret,
        )

        # Making the request
        payload = {"text" : text}

        # attach the media
        if media_id:
            payload["media"] = {"media_ids" : [media_id]}

        response = oauth.post(
            "https://api.twitter.com/2/tweets",
            json=payload,
        )

        if response.status_code != 201:
            print(f"Tweet failed with code {response.status_code}: {response.text}")
            return (False, response.text)
        return (True, "success")

