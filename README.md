# fgw_router_ext
Home Assistant device_tracker_ext implementation for the MEO router Altice Fiber Gateway GR241AG with or without MEO extenders
This version is compatible with the 2027.5 changes to the use of trackers in homeassistant but is not backward compatible with existing tracker entities you may already have.

![router](https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcQbkuz3EpT-XWHLZlPKgxVSAcrZgd8pn8j7yg&usqp=CAU)

### Installation
It's recommended to install this via [HACS](https://github.com/custom-components/hacs).
It isn't part of the defaults but you can add it by going to:

`HACS > Integrations > Custom repositories (on the top-right corner 3 dots)`

and adding:

Repository:<br/>
`dggithub1234/fgw_router_ext`<br/>
Category:<br/>
`Integration`

#### Restart Home assistant
Restart so the changes can take place.



### Configuration

#### Add the integration to the devices page

#### Setup the integration:

    host: 192.168.1.254
    port: 23
    (If you haven't changed the default credentials, these are the default ones)
    username: meo
    password: meo

Scan interval is set to 120s and new devices are set to disabled.
To change this edit the const.py file in the \config\custom_components\fgw_router_et\ directory and reload the integration.

#### Devices:
Trackers are automatically added and can be renamed in the UI.

You can also turn off automatic enabling of new devices in the UI as follows:
    Go to Settings 
    ➔ Devices & Services.
    Find your Altice / MEO FiberGateway integration card.
    Click the three vertical dots on the integration card.
    Click System options.
    Toggle OFF the setting that says "Enable newly added entities".
    Click Save.



