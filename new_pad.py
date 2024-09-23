from briefings_server import *
print("Started")
etherpad = py_etherpad.EtherpadLiteClient(apiKey=conf("etherpad.apikey"),baseUrl=conf("etherpad.url")+'/api')
print("Etherpad loaded")
prefix = SEMINAR_SERIES 
padid = (prefix+str(uuid.uuid4())).replace('-','')
#etherpad.copyPad(conf('etherpad.scheduletemplate'), padid)
print("Creating pad...")
etherpad.createPad(padid)
print("Created pad:")
print(padid)
