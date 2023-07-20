from requests_oauthlib import OAuth1Session
import json

def twitter_login():
    # Run this one-liner to populare the value "None" for the vakues of the twitter things in the ndatabase
    # echo 'INSERT INTO config (value, valuetype, key, help) VALUES ("None","str","twitter.consumer_key","Run python3 twitter.py for help with the login process.");INSERT INTO config (value, valuetype, key, help) VALUES ("None","str","twitter.consumer_secret","Run python3 twitter.py for help with the login process.");INSERT INTO config (value, valuetype, key, help) VALUES ("None","str","twitter.access_token","Run python3 twitter.py for help with the login process.");INSERT INTO config (value, valuetype, key, help) VALUES ("None","str","twitter.access_secret","Run python3 twitter.py for help with the login process.");' | sqlite3 oqe_config.sqlite

    print("Go here: https://developer.twitter.com/en/portal/projects-and-apps and click on the key that says \"keys and tokens\", and get the consumer key and secret.")
    consumer_key = input("Paste the Consumer Key here: ")
    consumer_secret = input("Paste the Consumer Secret here: ")
   
    # use the consumer key/secret to retrieve resource owner key/secret
    request_token_url = "https://api.twitter.com/oauth/request_token?oauth_callback=oob&x_auth_access_type=write"
    oauth = OAuth1Session(consumer_key, client_secret=consumer_secret)
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
        consumer_key,
        client_secret=consumer_secret,
        resource_owner_key=resource_owner_key,
        resource_owner_secret=resource_owner_secret,
        verifier=verifier,
    )
    oauth_tokens = oauth.fetch_access_token(access_token_url)

    access_token = oauth_tokens["oauth_token"]
    access_secret = oauth_tokens["oauth_token_secret"]
    print("Twitter login complete!")
    keys = {
            "twitter.consumer_key" : consumer_key,
            "twitter.consumer_secret" : consumer_secret,
            "twitter.access_token" : access_token,
            "twitter.access_secret" : access_secret
            }
    print("Save these keys: ")
    for key in keys:
        print(f"{key} : {keys[key]}")
    print("They're also being returned in the return value as a dict.")
    return keys


class Twitter:
    def __init__(self, keys):
        # Inputs:
        # keys: a dictionary with entries for the needed keys/secrets
        try:
            self.consumer_key = keys["consumer_key"]
            self.consumer_secret = keys["consumer_secret"]
            self.access_token = keys["access_token"]
            self.access_secret = keys["access_secret"]
        except:
            self.consumer_key = None
            self.consumer_secret = None
            self.access_token = None
            self.access_secret = None
       

    def upload_image(self, image_bytes,image_type="png", log=None):
        # Input:
        # media: a bytes object containing a png image
        # Output:
        # A media_id string referring to the uploaded image or None if the upload failed

        # according to the documentation this is supposed to be a 3-step process with an "INIT" then one or more "APPEND" then a "FINALIZE".
        # I've found experimentally it doesn't really work that way and talk on github confirms the API does not behave as documented.
        # Regardless, this code seems to work as of 7/14/2023
        logtext = None
        try:
            request_data = {
                    "command" : "FINALIZE",
                    "media_type" : f"image/{image_type}", # TODO support other types if necessary
                    "total_bytes" : len(image_bytes),
                    "media_category" : "tweet_image"
                    }
            files = {
                    "media":image_bytes
                    }
            media_id = None
            oauth = OAuth1Session(
                self.consumer_key,
                client_secret=self.consumer_secret,
                resource_owner_key=self.access_token,
                resource_owner_secret=self.access_secret,
            )

            response = oauth.post('https://upload.twitter.com/1.1/media/upload.json', json=request_data, files=files)
            media_id = None
            if not response.ok:
                logtext = f"twitter media upload failed due to response {response.status_code} : {response.text}"
                return None
            else:
                return str(response.json()["media_id"])
        except Exception as e:
            logtext = f("twitter media upload failed due to exception {e}")
            return None
        finally:
            if logtext is not None:
                if log is not None:
                    log.error(logtext)
                else:
                    print(logtext)


    def tweet(self, text, media_id=None, log=None):
        # Input:
        # text: A string containing the body of the tweet.
        # media_id: A string containing a media_id for an image to be attached (e.g. the return value from an upload_media call).  If None then no image will be attached.
        # Output:
        # a boolean True/False indicating if the tweet was made successfully.

        logtext = None
        try:
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
                logtext = f"tweet failed due to response {response.status_code} : {response.text}"
                return False
            return True
        except Exception as e:
            logtext = f"tweet failed due to exception {e}"
            return False
        finally:
            if logtext is not None:
                if log is not None:
                    log.error(logtext)
                else:
                    print(logtext)

if __name__ == "__main__":
    twitter_login()
