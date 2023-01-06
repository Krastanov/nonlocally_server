from briefings_server import *
etherpad = py_etherpad.EtherpadLiteClient(apiKey=conf("etherpad.apikey"),baseUrl=conf("etherpad.url")+'/api')
prefix = SEMINAR_SERIES 
padid = (prefix+str(uuid.uuid4())).replace('-','')
#etherpad.copyPad(conf('etherpad.scheduletemplate'), padid)
etherpad.createPad(padid)
print("Created pad:")
print(padid)
