READ_ME = '''
=== PREREQUISITES ===
Run in Python 3.6 or Later

Install requests and Meraki Dashboard API Python modules:
pip[3] install requests [--upgrade]
pip[3] install trio [--upgrade]
pip[3] install asks [--upgrade]
pip[3] install colorlog [--upgrade]
pip[3] install pandas [--upgrade]



=== DESCRIPTION ===
This script asyncronously finds all clients in an org and outputs to a CSV file

For questions, contact Josh at jleatham@cisco.com.


=== USAGE ===
python3 findClient.py parameters.ini

parameters.ini should include:
[access]
key = 1234|YOUR-APIKEY|5678
org = 12|ORGID|34

=== OUTPUT ===
findClient_log...   #shows all API calls and errors
clients...csv         #final report, formatted, time stamped
temp_clients...csv    # in case timeouts or errors, csv will include what it can.  Is deleted on 
                    # successful completion of rogue..csv file
'''



from meraki import meraki
import requests
from datetime import datetime
import asks
import time
import trio
import pandas as pd
import configparser
import csv
import os
import sys
from datetime import datetime
import logging
from colorlog import ColoredFormatter
import json


def main(api_key, org_id):


    headers = {'X-Cisco-Meraki-API-Key': api_key, 'Content-Type': 'application/json'}

    
    #sync get all serials in all networks, return networkSerials dict {'D-serial':['NID','d-mac','network name','model','device name']}
    networkSerials = get_serials_of_devices(api_key, org_id)
    
    #async loop and get all clients in each device, pass in networkSerials, 
    # Return results {'D-serial':['200',{clientsInNetwork},['NID','d-mac','Network Name','model','device name']]}
        #notice we carry through the device info for each previous API call so that we cut down on processing later
    url = 'https://api.meraki.com/api/v0/devices/{0}/clients?timespan=86400'
    results = trio.run(get_meraki_api_async, networkSerials, url, headers)

    #take results and convert to clientDict for each individual client: {'client MAC':['N_1234','Network Name','usage:sent','usage:received','mdns','dhcp','switchport']}
    clientDict = process_macs_of_clients(results)

    #async get all client data per network using the client MAC.  
    # Return results: {'client MAC: [200,{detailedAllClients}, ['N_1234','Network Name','usage:sent','usage:received','mdns','dhcp','switchport']]}
    url = 'https://api.meraki.com/api/v0/networks/{0}/clients/{1}'
    results = trio.run(v2_get_meraki_api_async, clientDict, url, headers)

    #Take results and associated client and network data and prep for CSV
    df = process_client_data(results)
    
    #take client data and map to their recentDevice
    df = add_recent_seen_device(df,networkSerials)

    #finalize a few fields, and write to CSV
    write_csv(df)


async def v2_get_meraki_api_async(input, url, headers ):

    #take the input dict and iterate the APIs based on the keys
    #pass the value of each key to the results dict 
    #API loop will pass through all APIs until all possible APIs comeback 200 status code
    
 
    apiLoop = 1000000000 #arbitrary large number
    results = {}
    while (len(input) < apiLoop) and len(input) is not 0:
        apiLoop = len(input)
        async with trio.open_nursery() as nursery:
            for key, value in input.items():
                netID = value[0]
                nursery.start_soon(fetch, url.format(netID,key), headers, results, key, value)
    
        #nursery will run all async items at once, then move on syncronously
        #fetch results gets placed into results dictionary when done
        input = {} 
        for key, result in results.items():
            #rebuilds the dict to what it was before
            #but only include non successful API calls
            if result[0] is not 200:
                input[key] = result[2] 
                #networkIds.append(key)
                #print(key)
        #print(networkIds)

        success_count = 0
        fail_count = 0
        for key, result in results.items():
            if result[0] is 200:
                success_count += 1
            else:
                #print(key)
                fail_count += 1
        logger.info('Success count: {0}'.format(str(success_count)))
        logger.warn('Fail count: {0}'.format(str(fail_count)))

    return results    




async def get_meraki_api_async(input, url, headers ):

    #take the input dict and iterate the APIs based on the keys
    #pass the value of each key to the results dict 
    #API loop will pass through all APIs until all possible APIs comeback 200 status code
    
 
    apiLoop = 1000000000 #arbitrary large number
    results = {}
    while (len(input) < apiLoop) and len(input) is not 0:
        apiLoop = len(input)
        async with trio.open_nursery() as nursery:
            for key, value in input.items():
                nursery.start_soon(fetch, url.format(key), headers, results, key, value)
    
        #nursery will run all async items at once, then move on syncronously
        #fetch results gets placed into results dictionary when done
        input = {} 
        for key, result in results.items():
            #rebuilds the dict to what it was before
            #but only include non successful API calls
            if result[0] is not 200:
                input[key] = result[2] 
                #networkIds.append(key)
                #print(key)
        #print(networkIds)

        success_count = 0
        fail_count = 0
        for key, result in results.items():
            if result[0] is 200:
                success_count += 1
            else:
                #print(key)
                fail_count += 1
        logger.info('Success count: {0}'.format(str(success_count)))
        logger.warn('Fail count: {0}'.format(str(fail_count)))

    return results    



async def fetch(url, headers, results, key, value):
    logger.debug("Start: {0}".format(url))
    #response = await asks.get(url, headers=headers)
    response = await s.get(url, headers=headers)
    #async with s.get(url, headers=headers) as response:
    if response.status_code == 200:
        logger.info("Finished: {0} {1} {2}".format(url, str(len(response.content)), response.status_code))
    elif response.status_code == 429:
        logger.warn("Retry Later: {0} {1} {2}".format(url, str(len(response.content)), response.status_code))
        #Pause for 1 second to get under 5 API/sec limit
        time.sleep(1) #not sure why this works as opposed to 'await trio.sleep(1)'
    else:
        logger.error("Failed: {0} {1} {2}".format(url, str(len(response.content)), response.status_code))
        return
    
    #write to dictionary {'NetId' : [data]}
    results[key] = [response.status_code, response.content, value]



def get_serials_of_devices(api_key, org_id):
    '''
    [Get networks in org
        {
            "id": "N_24329156",
            "organizationId": 2930418,
            "name": "My organization",
            "timeZone": "America/Los_Angeles",
            "tags": " tag1 tag2 ",
            "type": "combined",
            "disableMyMerakiCom": false
        }
    ]   

    [Get devices in org
        {
            "mac": "00:11:22:33:44:55",
            "serial": "Q234-ABCD-5678",
            "networkId": "N_24329156",
            "model": "MR34",
            "claimedAt": 1518365681.0,
            "publicIp": "123.123.123.1",
            "name": "My AP"
        }
    ]
    '''      

    #get network names first and map to IDs
    temp = {}  #{'N_12345':'test network'}
    #networkIds = []
    networks = meraki.getnetworklist(api_key, org_id, suppressprint=True)
    for network in networks:
        if 'name' in network:
            temp[network['id']] = network['name']
    inventory = meraki.getorginventory(api_key, org_id, suppressprint=True)
    #get all APs in org
    #create new Dict, only include networks with MRs, and add serial to data
    #devices = [device for device in inventory if device['model'][:2] in ('MR') and device['networkId'] is not None]
    networkIdDict = {} #{'D-serial':['NID','mac','network name','model','device name']}
    for device in inventory:
        if device['serial'] and device['networkId']:
            #networkIdDict[device['networkId']] = [device['mac'],device['serial'],device['model'],temp[device['networkId']]] 
            networkIdDict[device['serial']] = [device['networkId'],device['mac'],temp[device['networkId']],device['model'],device['name']]
    return networkIdDict


def process_macs_of_clients(results):
    '''
        [
            {
                "usage": {
                    "sent": 138.0,
                    "recv": 61.0
                    },
                "id": "k74272e",
                "description": "Miles's phone",
                "mdnsName": "Miles's phone",
                "dhcpHostname": "MilesPhone",
                "mac": "00:11:22:33:44:55",
                "ip": "1.2.3.4",
                "vlan": "",
                "switchport": null
            }
        ]
    '''
    clientDict = {}  
    #for each serial of HW device, find all the associated clients  
    for key, result in results.items():
        #{'serial':['200',{data},['N_1234','AB:CD...','Network Name','model','device name']]}
        netID = result[2][0]
        netName = result[2][2]
        model = result[2][3]
        deviceName = result[2][4]        
        clientData = result[1]
        clientData = json.loads(clientData)
        #s = json.dumps(clientData, indent=4, sort_keys=True)
        #print(s)
        for client in clientData:
            mdnsName = client['mdnsName']
            dhcpHostname = client['dhcpHostname']
            switchport = client['switchport']
            sent = client['usage']['sent']
            recv = client['usage']['recv']
            mac = client['mac']
            #load client data into new dict
            #{'client MAC':['N_1234','Network Name','usage:sent','usage:received','mdns','dhcp','switchport']}
            clientDict[mac] = [netID,netName,sent,recv,mdnsName,dhcpHostname,switchport]
    return clientDict








def process_client_data(results):
    '''
    {client data
    "id": "k74272e",
    "description": "Miles's phone",
    "mdnsName": "Miles's phone",
    "dhcpHostname": "MilesPhone",
    "mac": "00:11:22:33:44:55",
    "ip": "1.2.3.4",
    "vlan": "255",
    "switchport": null,
    "ip6": "",
    "firstSeen": 1518365681,
    "lastSeen": 1526087474,
    "manufacturer": "Apple",
    "os": "iOS",
    "user": "null",
    "ssid": "My SSID",
    "wirelessCapabilities": "802.11ac - 2.4 and 5 GHz",
    "smInstalled": true,
    "recentDeviceMac": "00:11:22:33:44:55",
    "clientVpnConnections": [
        {
        "remoteIp": "1.2.3.4",
        "connectedAt": 1522613355,
        "disconnectedAt": 1522613360
        }
    ],
    "lldp": [
        [
        "System name",
        "Some system name"
        ],[]
    ],
    "cdp": null
    }    
    '''

    
    logger.debug('Preparing the output file. Check your local directory.')
    timenow = '{:%Y%m%d_%H%M%S}'.format(datetime.now())
    
    #####Added CSV writer so that CSV file would write while performing API iteration
    ##### Helps for when there are unexpected crashes or timeouts, we can still have some data
    temp_filename = 'temp_clients_{0}.csv'.format(timenow)
    output_file = open(temp_filename, mode='w', newline='\n')
    #field_names = ['Network Name','SSID','Channels','Device SN','Network ID','First Seen','Last Seen','Plugged in Time']
    field_names = ['network', 'networkId', 'id', 'description', 'mdnsName', 'dhcpHostname', 'mac', 'ip', 'ip6',
                    'sent', 'recv', 'firstSeen', 'lastSeen', 'manufacturer', 'os', 'user', 'vlan', 'switchport',
                    'ssid', 'wirelessCapabilities', 'smInstalled', 'recentDeviceMac', 'recentDeviceSerial',
                    'recentDeviceName', 'recentDeviceModel', 'clientVpnConnections', 'lldp', 'cdp']    
    csv_writer = csv.DictWriter(output_file, fieldnames=field_names, restval='')
    csv_writer.writeheader()
    ###### End CSV init
    n = 1000  #used to print screen every n rogue ID found
    x = 0
    
    #for each client, pull the data needed
    #results: {'client MAC: [200,data, ['N_1234','Network Name','usage:sent','usage:received','mdns','dhcp','switchport']]}
    #client data lives in the data variable, 
    for key, result in results.items():
        try:
            network = result[2][1]
            networkId = result[2][0]        
            mdnsName = result[2][4]
            dhcpHostname = result[2][5]
            sent = result[2][2]
            recv = result[2][3]        
            client = result[1]
            client = json.loads(client)
            #s = json.dumps(client, indent=4, sort_keys=True)
            #print(s)
            #print('\n')        
            if 'id' in client:
                id = client['id']
            else:
                id = ''
            description = client['description']
            mac = client['mac']
            ip = client['ip']
            ip6 = client['ip6']
            if 'switchport' in client:
                switchport = client['switchport']
            else:
                switchport = ''   
            if 'ssid' in client:
                ssid = client['ssid']
            else:
                ssid = ''                 
            vlan = client['vlan']
            firstSeen = client['firstSeen']
            lastSeen = client['lastSeen']
            manufacturer = client['manufacturer']
            client_os = client['os']
            user = client['user']
            wirelessCapabilities = client['wirelessCapabilities']
            clientVpnConnections = client['clientVpnConnections']
            lldp = client['lldp']
            cdp = client['cdp']
            smInstalled = client['smInstalled']
            recentDeviceMac = client['recentDeviceMac']

            data = {'network':network , 'networkId':networkId , 'id':id , 'description':description , 'mdnsName':mdnsName , 'dhcpHostname':dhcpHostname , 'mac':mac , 'ip':ip , 'ip6':ip6 ,
                    'sent':sent , 'recv':recv , 'firstSeen':firstSeen , 'lastSeen':lastSeen , 'manufacturer':manufacturer , 'os':client_os , 'user':user , 'vlan':vlan , 'switchport':switchport ,
                    'ssid':ssid , 'wirelessCapabilities':wirelessCapabilities , 'smInstalled': smInstalled, 'recentDeviceMac':recentDeviceMac , 'recentDeviceSerial':'' ,
                    'recentDeviceName':'' , 'recentDeviceModel':'' , 'clientVpnConnections':clientVpnConnections , 'lldp':lldp , 'cdp':cdp }
            #data={'Network Name':networkName,'SSID':ssidName,'Channels':channels,'Device SN':deviceSN,'Network ID':networkID,'First Seen':firstSeen,'Last Seen':lastSeen,'Plugged in Time':wiredLast}
            csv_writer.writerow(data)
            #output to screen every n iterations of CSV lines
            if x % n == 0:
                if x == 0:
                    logger.info("Processing Clients")
                else:
                    logger.info("Processing Clients.  Count = {0}".format(str(x)))
                #print("Processing Rogue SSIDs.  Count = "+str(x))
            x += 1
        except Exception as e:
            print(e)
            logger.error("Error processing clientData")


    
    output_file.close() #close out temp_rogue file    
    df = pd.read_csv(temp_filename)   #reopen temp csv and import to pandas for processing
    #remove temp CSV file because final report was generated
    
    if os.path.exists(temp_filename):
        os.remove(temp_filename)
    else:
        #print("The file does not exist") 
        logger.error("The temp CSV file does not exist")       
    
    return df


def add_recent_seen_device(df,networkSerials):
    #NetworkSerials: {'D-serial':['NID','d-mac','network name','model','device name']}
    #convert dictionary to be based on MACs, since that is what we have
    #could do a double loop iterating both datasets at a time, not sure if a better way
    networkMacs = {}
    for key,value in networkSerials.items():
        networkMacs[value[1]] = [key,value[0],value[2],value[3],value[4]]
    #NetworkMacs: {'d-mac':['d-serial','NID', 'network name','model','device name']}
    #need to fill in 'recentDeviceSerial', 'recentDeviceName', 'recentDeviceModel' 
    for i in df.index:
        if df.ix[i,'recentDeviceMac']:
            df.ix[i,'recentDeviceName']   = networkMacs[df.ix[i,'recentDeviceMac']][4]
            df.ix[i,'recentDeviceSerial'] = networkMacs[df.ix[i,'recentDeviceMac']][0]
            df.ix[i,'recentDeviceModel']  = networkMacs[df.ix[i,'recentDeviceMac']][3]
    return df


def add_network_name(api_key, org_id, df):
    #start_time = datetime.now()
    logger.debug("Adding Network names to CSV file")
    networkNameList = {}
    networks = meraki.getnetworklist(api_key, org_id, suppressprint=True)
    for network in networks:
        if 'name' in network:
            networkNameList[network['id']] = network['name']
            #networkNameList.append([network['id'], network['name']])
    for i in df.index:
        #df.ix[...] is just a locator , i is the index of the row
        #networkNameList is a dictionary, so networkNameList[ID] will point to the name pair {'ID':'Name}
        df.ix[i,'Network Name'] = networkNameList[df.ix[i,'Network ID']]

    #print('time: {0}'.format(datetime.now()-start_time))
    return df  

def convert_dates(df):
    #converts meraki API dates(in seconds format) into a datetime stamp
    df['firstSeen'] = pd.to_datetime(df['firstSeen'],unit='s')
    df['lastSeen'] = pd.to_datetime(df['lastSeen'],unit='s')
    #remove 0's from data, otherwise it will mess with datatimestamp
    '''
    for i in df.index:
        if df.ix[i,'Plugged in Time'] == 0:
            df.ix[i,'Plugged in Time'] = ''    
    df['Plugged in Time'] = pd.to_datetime(df['Plugged in Time'],unit='s')
    '''
    return df


def write_csv(df):
    timenow = '{:%Y%m%d_%H%M%S}'.format(datetime.now())
    filename = 'clients_{0}.csv'.format(timenow)
    df = convert_dates(df)    #format DateTime to be consistent and readable
    #reorder CSV data
    df = df[['network', 'networkId', 'id', 'description', 'mdnsName', 'dhcpHostname', 'mac', 'ip', 'ip6',
                    'sent', 'recv', 'firstSeen', 'lastSeen', 'manufacturer', 'os', 'user', 'vlan', 'switchport',
                    'ssid', 'wirelessCapabilities', 'smInstalled', 'recentDeviceMac', 'recentDeviceSerial',
                    'recentDeviceName', 'recentDeviceModel', 'clientVpnConnections', 'lldp', 'cdp']]
    df = df.reset_index(drop=True) #drop dataframe index
    df = df.sort_values(['network'])  #sort CSV by network name
    df.to_csv(filename,index=False)  #write to final rogue CSV file

# Prints READ_ME help message for user to read
def print_help():
    lines = READ_ME.split('\n')
    for line in lines:
        print('# {0}'.format(line))



def configure_logging():
    logger = logging.getLogger(__name__)
    logging.basicConfig(
        filename='{}_log_{:%Y%m%d_%H%M%S}.txt'.format(sys.argv[0].split('.')[0], datetime.now()),
        level=logging.DEBUG,
        format='%(asctime)s: %(levelname)7s: [%(name)s]: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Define a Handler which writes INFO messages or higher to the sys.stderr
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    # Set a format which is simpler for console use
    formatter = logging.Formatter('%(name)-12s: %(levelname)-8s %(message)s')
    # Tell the handler to use this format
    console.setFormatter(formatter)
    # Add the handler to the root logger
    logging.getLogger('').addHandler(console)  
    return logger

def configure_colorLogging():

    LOG_LEVEL = logging.DEBUG
    LOGFORMAT = "%(asctime)s: %(log_color)s%(levelname)-8s%(reset)s | %(log_color)s%(message)s%(reset)s"
    logging.root.setLevel(LOG_LEVEL)
    logging.basicConfig(
        filename='{}_log_{:%Y%m%d_%H%M%S}.txt'.format(sys.argv[0].split('.')[0], datetime.now()),
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    formatter = ColoredFormatter(LOGFORMAT)
    stream = logging.StreamHandler()
    stream.setLevel(LOG_LEVEL)
    stream.setFormatter(formatter)
    logger = logging.getLogger('')
    logger.setLevel(LOG_LEVEL)
    logger.addHandler(stream)

    #logger.debug("A quirky message only developers care about")
    #logger.info("Curious users might want to know this")
    #logger.warn("Something is wrong and any user should be informed")
    #logger.error("Serious stuff, this is red for a reason")
    #logger.critical("OH NO everything is on fire")
    return logger

def read_config():

    inputs = sys.argv[1:]
    if len(inputs) == 0:
        print_help()
        sys.exit(2)
    file = inputs[0]

    cp = configparser.ConfigParser()
    try:
        cp.read(file)
        api_key = cp.get('access', 'key')
        org_id = cp.get('access', 'org')
    except:
        print_help()
        sys.exit(2)
    return api_key, org_id





if __name__ == "__main__":

    #https://asks.readthedocs.io/en/latest/
    asks.init("trio")
    s = asks.Session(connections=3)
    #s = asks.Session('https://some-web-service.com',connections=20)

    start_time = datetime.now()
    start = int(time.time())
    # Configure logging to stdout
    #logger = configure_logging()
    logger = configure_colorLogging()
    # Output to logfile/console starting inputs  
    logger.debug('Started script at {0}'.format(start_time))

    #parse input file.
    api_key, org_id = read_config()

    #Execute the program
    main(api_key, org_id)


    # Finish output to logfile/console
    end_time = datetime.now()
    end = int(time.time())
    logger.debug('Ended script at {0}'.format(end_time))
    d = divmod(end - start,86400)  # days
    h = divmod(d[1],3600)  # hours
    m = divmod(h[1],60)  # minutes
    s = m[1]  # seconds
    logger.debug('Total run time = {0} minutes , {1} seconds'.format(m[0],s))

