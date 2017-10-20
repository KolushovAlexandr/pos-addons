===============================
 POS Multi-session Sync Server
===============================

Installation
============

* `Install <https://odoo-development.readthedocs.io/en/latest/odoo/usage/install-module.html>`__ this module in a usual way

Separate Sync Server
--------------------

In case you use second server, there might be an 'Access-Control-Allow-Origin' error. Your web server has to add additional header to response. Configuration for nginx may look as following::

        add_header 'Access-Control-Allow-Origin' * always;

To make your second server be able to process 'OPTIONS' method requests, nginx configuration has to consist following::

        if ($request_method = 'OPTIONS') {
                add_header 'Access-Control-Allow-Origin' '*';
                add_header 'Access-Control-Allow-Methods' 'GET, POST, OPTIONS';
                add_header 'Access-Control-Allow-Headers' 'DNT,X-CustomHeader,Keep-Alive,User-Agent,X-Requested-With,If-Modified-Since,Cache-Control,Content-Type,Content-Range,Range,X-Debug-Mode';
                add_header 'Access-Control-Max-Age' 1728000;
                add_header 'Content-Type' 'text/plain; charset=utf-8';
                add_header 'Content-Length' 0;
                return 204;
        }

In order to configure access to the sync server do the following on a server:

* `Activate Developer Mode <https://odoo-development.readthedocs.io/en/latest/odoo/usage/debug-mode.html>`__
* Open menu ``Settings >> Parameters >> System Parameters``
* Click ``[Create]``

    * Paste in the field **Key** 'pos_longpolling.allow_public'
    * Paste in the field **Value** '1'

* Click ``[Save]``

Configuration
=============

Separate sync server
--------------------

In main server configure sync server:

* Open menu ``Point of Sale``
* Click ``Configuration >> Point of Sale``
* Click on a POS belongs to required for syncing Multi-session
* Click ``[Edit]``
* Paste an external server url in the field **Sync Server**
* Click ``[Save]``
