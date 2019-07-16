# -*- coding: utf-8 -*-
# Copyright 2017 Ivan Yelizariev <https://it-projects.info/team/yelizariev>
# Copyright 2017, 2019 Kolushov Alexandr <https://it-projects.info/team/KolushovAlexandr>
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

import logging
import json
import time

from odoo import api
from odoo import fields
from odoo import models

_logger = logging.getLogger(__name__)


class PosConfigSync(models.Model):
    _name = 'pos_multi_session_sync.pos'

    multi_session_ID = fields.Integer('Multi-session', index=True,
                                      help='Set the same value for POSes where orders should be synced. '
                                           'Keep empty if this POS should not use syncing. '
                                           'Before updating it you need to close active session')
    multi_session_message_ID = fields.Integer(index=True, default=1, string="Last sent Multi-session message number")
    pos_ID = fields.Integer(index=True, string='POS')
    user_ID = fields.Integer(index=True)


class PosMultiSessionSync(models.Model):
    _name = 'pos_multi_session_sync.multi_session'

    multi_session_ID = fields.Integer('Multi-session', index=True,
                                      help='Set the same value for POSes where orders should be synced. '
                                           'Keep empty if this POS should not use syncing. '
                                           'Before updating it you need to close active session')
    order_ID = fields.Integer(index=True, string='Order')
    run_ID = fields.Integer(index=True, string='Session', help='Actual run_id of the multi_session')
    dbname = fields.Char(index=True)

    @api.multi
    def on_update_message(self, message):
        self.ensure_one()
        if message['data']['run_ID'] > self.run_ID:
            self.prepare_new_session(message)

        if message['action'] == 'update_order':
            res = self.set_and_broadcast_order(message)
        elif message['action'] == 'sync_all':
            res = self.get_sync_all(message)
        elif message['action'] == 'remove_order':
            res = self.remove_order(message)
        else:
            res = self.broadcast_message(message)
        return res

    @api.multi
    def prepare_new_session(self, message):
        self.ensure_one()
        run_ID = message['data']['run_ID']
        self.write({
            'run_ID': run_ID,
        })
        old_orders = self.env['pos_multi_session_sync.order'].search([('multi_session_ID', '=', self.multi_session_ID),
                                                                      ('state', '=', 'draft'),
                                                                      ('run_ID', '<', run_ID)])
        if old_orders:
            old_orders.write({'state': 'unpaid'})
            self.write({'order_ID': 0})

    @api.multi
    def check_order_revision(self, message, order):
        self.ensure_one()
        client_revision_ID = message['data']['revision_ID']
        server_revision_ID = order.revision_ID
        if not server_revision_ID:
            server_revision_ID = 1
        _logger.debug('Client revision ID %s: Server revision ID %s', client_revision_ID, server_revision_ID)
        _logger.debug('Client nonce %s: Server nonce %s', message['data']['nonce'], order.nonce)
        if int(client_revision_ID) != server_revision_ID:
            _logger.debug('Client revision is not server revision')
            if message['data']['nonce'] == order.nonce:
                return 'nonce'
            return False
        else:
            return True

    @api.multi
    def dict_compare(self, d1, d2):
        self.ensure_one()
        d1_keys = set(d1.keys())
        d2_keys = set(d2.keys())
        intersect_keys = d1_keys.intersection(d2_keys)

        # ID does not need to compare because the line can be
        # with different id as each POS creates its own separate line

        # is_changed does not need to be compared because this value is not updated after being written to the server
        added = d1_keys - d2_keys
        added.discard('is_changed')

        removed = d2_keys - d1_keys
        removed.discard('is_changed')

        modified = {o: (d1[o], d2[o]) for o in intersect_keys if d1[o] != d2[o]}
        if modified.get('id'):
            modified.pop('id')

        return modified, added, removed

    @api.multi
    def set_changes(self, message, order):
        self.ensure_one()
        order = json.loads(order.order)
        data = order.get('data')
        lines = data.get('lines')
        lines_list = {l[2]['uid']: l[2] for l in lines}

        for e in message['data']['lines']:
            line = lines_list.get(e[2]['uid'])
            if line:
                modified, added, removed = self.dict_compare(e[2], line)
                if modified or added or removed:
                    e[2]['is_changed'] = True
        return message

    @api.multi
    def broadcast_order_update(self, order, message):
        self.broadcast_message(message)
        return {'action': 'update_revision_ID', 'revision_ID': order.revision_ID,
                'order_ID': message['data']['sequence_number'], 'run_ID': order.run_ID}

    @api.multi
    def set_and_broadcast_order(self, message):
        self.ensure_one()

        order, updated_message = self.set_order(message)

        if not order:
            return updated_message

        return self.broadcast_order_update(order, updated_message)

    @api.multi
    def set_order(self, message, force=False):
        self.ensure_one()
        order_uid = message['data']['uid']
        sequence_number = message['data']['sequence_number']
        order = self.env['pos_multi_session_sync.order'].search([('order_uid', '=', order_uid)])
        revision = self.check_order_revision(message, order)
        run_ID = order.run_ID or message['data']['run_ID'] or False

        if revision == "nonce":
            return (False, {'action': ''})
        elif (not revision and not force) or (order and order.state == 'deleted'):
            _logger.debug('Revision error %s %s', order_uid, order.state)
            return (False, {'action': 'revision_error', 'order_uid': order_uid, 'state': order.state})

        if order:  # order already exists
            message = self.set_changes(message, order)
            order.write({
                'order': json.dumps(message),
                'revision_ID': order.revision_ID + 1,
                'run_ID': message['data']['run_ID'],
                'nonce': message['data']['nonce']
            })
        else:
            if self.order_ID + 1 != sequence_number:
                sequence_number = self.order_ID + 1
                message['data']['sequence_number'] = sequence_number
            order = order.create({
                'order': json.dumps(message),
                'order_uid': order_uid,
                'multi_session_ID': self.multi_session_ID,
                'run_ID': run_ID,
                'nonce': message['data']['nonce']
            })
            self.write({'order_ID': sequence_number})

        message['data']['revision_ID'] = order.revision_ID

        return (order, message)

    @api.multi
    def get_sync_all(self, message):
        self.ensure_one()
        pos_ID = message['data']['pos_id']
        user_ID = self.env.context.get("user_ID")
        pos = self.env['pos_multi_session_sync.pos'].search([("pos_ID", "=", pos_ID)])
        run_ID = message['data']['run_ID']
        if len(pos) == 1:
            # pos.config was assigned to a different multi session
            pos.multi_session_ID = self.multi_session_ID
        elif len(pos) > 1:
            # in case there were already created plenty same_id_poses (before the fix)
            pos.unlink()
            pos = None
        if not pos:
            pos = self.env['pos_multi_session_sync.pos'] \
                .create({'multi_session_ID': self.multi_session_ID, 'pos_ID': pos_ID, 'user_ID': user_ID})
        if pos.user_ID != user_ID:
            pos.user_ID = user_ID
        orders = []
        uid = message.get('uid')
        message_ID = pos.multi_session_message_ID + 1
        pos.write({
            'multi_session_message_ID': message_ID
        })
        if uid:
            order_uid = message['uid']
            order = self.env['pos_multi_session_sync.order'].search([('order_uid', '=', order_uid)])
            msg = json.loads(order.order)
            msg['data']['message_ID'] = pos.multi_session_message_ID
            msg['data']['revision_ID'] = order.revision_ID
            _logger.debug('Sync All: Server revision ID %s', order.revision_ID)
            msg['data']['run_ID'] = run_ID
            orders.append(msg)
        else:
            for order in self.env['pos_multi_session_sync.order'] \
                             .search([('multi_session_ID', '=', self.multi_session_ID), ('state', '=', 'draft'),
                                      ('run_ID', '=', run_ID)]):
                msg = json.loads(order.order)
                msg['data']['message_ID'] = pos.multi_session_message_ID
                msg['data']['revision_ID'] = order.revision_ID
                msg['data']['run_ID'] = run_ID
                orders.append(msg)

        data = {
            'action': 'sync_all',
            'data': {
                'orders': orders,
                'message_ID': pos.multi_session_message_ID,
                'order_ID': self.order_ID,
                'uid': uid,
                'login_number': message['login_number'],
                'session_id': message['session_id'],
            }
        }

        if message.get('immediate_rerendering'):
            return data
        else:
            self.send_sync_message(data)
            return {}

    @api.multi
    def remove_order(self, message):
        self.ensure_one()
        order_uid = message['data']['uid']

        order = self.env['pos_multi_session_sync.order'].search([('order_uid', '=', order_uid)])

        order_data = message['data'].get('order_data', False)

        if not order and order_data:
            # order paid in offline
            order_data = json.loads(order_data)
            order_data['data']['nonce'] = 'paid offline'
            order, order_data = self.set_order(order_data)
            order_uid = order.order_uid

        revision = self.check_order_revision(message, order)
        # if revision is True then it means order was not changed from online since last changes
        if order.state is not 'deleted' and not revision:
            if message['data']['finalized'] and order_data:
                order_data = json.loads(order_data)
                # example: order paid offline, but there were some additions to the order from online poses
                # we compare paid order to existing one in order to split it up onto paid and not paid parts
                server_order = json.loads(order.order)
                server_order_lines = [i[2] for i in server_order['data']['lines']]
                order_data_lines = [i[2] for i in order_data['data']['lines']]
                server_order_lines_uids = [(i['uid'], i['product_id']) for i in server_order_lines]
                order_data_lines_uids = [(i['uid'], i['product_id']) for i in order_data_lines]
                # TODO: quantities and any other modes are not counted
                not_paid_lines_uids = [l[0] for l in list(set(server_order_lines_uids) - set(order_data_lines_uids))]

                if len(not_paid_lines_uids):
                    # save paid part as a new order
                    order_data['data']['nonce'] = 'partially_paid'
                    # change uid because otherwise the existing updated order will be removed
                    old_order_uid = order_data['data']['uid']
                    # order_data['data']['uid'] = old_order_uid + '-part'
                    import wdb
                    wdb.set_trace()
                    partially_paid_order, pp_order_data = self.set_order(order_data, force=True)
                    if not partially_paid_order:
                        return {'action': 'revision_error', 'order_uid': old_order_uid}
                    partially_paid_order.write({
                        'state': 'deleted',
                    })
                    # update existing order with paid orderlines
                    updated_order_lines = [[0, 0, l] for l in server_order_lines if l['uid'] in not_paid_lines_uids]
                    server_order['data']['lines'] = updated_order_lines
                    order.write({
                        'order': json.dumps(server_order),
                    })
                    # import wdb
                    # wdb.set_trace()
                    return self.broadcast_order_update(order, order_data)
            else:
                return {'action': 'revision_error', 'order_uid': order_uid}
        if order:
            order.state = 'deleted'
        _logger.debug('Remove Order: %s Finalized: %s Revision: %s',
                      order_uid, message['data']['finalized'], message['data']['revision_ID'])
        self.broadcast_message(message)
        return {'order_ID': self.order_ID}

    @api.multi
    def send_sync_message(self, message):
        self.ensure_one()
        notifications = []
        channel_name = "pos.multi_session"
        for pos in self.env['pos_multi_session_sync.pos'].search([('user_ID', '=', self.env.context.get('user_ID')),
                                                                  ('multi_session_ID', '=', self.multi_session_ID)]):
            message['data']['message_ID'] = pos.multi_session_message_ID
            self.env['pos.config']._send_to_channel_by_id(self.dbname, pos.pos_ID, channel_name, message)

        if self.env.context.get('phantomtest') == 'slowConnection':
            _logger.info('Delayed notifications from %s: %s', self.env.user.id, notifications)
            # commit to update values on DB
            self.env.cr.commit()
            time.sleep(3)

        return 1

    @api.multi
    def broadcast_message(self, message):
        self.ensure_one()
        notifications = []
        channel_name = "pos.multi_session"
        for pos in self.env['pos_multi_session_sync.pos'].search([('multi_session_ID', '=', self.multi_session_ID)]):
            message_ID = pos.multi_session_message_ID + 1
            pos.write({
                'multi_session_message_ID': message_ID
            })
            message['data']['message_ID'] = message_ID
            self.env['pos.config']._send_to_channel_by_id(self.dbname, pos.pos_ID, channel_name, message)

        if self.env.context.get('phantomtest') == 'slowConnection':
            _logger.info('Delayed notifications from %s: %s', self.env.user.id, notifications)
            # commit to update values on DB
            self.env.cr.commit()
            time.sleep(3)

        return 1


class PosMultiSessionSyncOrder(models.Model):
    _name = 'pos_multi_session_sync.order'

    order = fields.Text('Order JSON format')
    nonce = fields.Char('Random nonce')
    order_uid = fields.Char(index=True)
    state = fields.Selection([('draft', 'Draft'), ('deleted', 'Deleted'), ('unpaid', 'Unpaid and removed')], default='draft', index=True)
    revision_ID = fields.Integer(default=1, string="Revision", help="Number of updates received from clients")
    multi_session_ID = fields.Integer(default=0, string='Multi session')
    pos_session_ID = fields.Integer(index=True, default=0, string='POS session')
    run_ID = fields.Integer(index=True, string="Running count", default=1,
                            help="Number of Multi-session starts. "
                                 "It's incremented each time the last session in Multi-session is closed. "
                                 "It's used to prevent synchronization of old orders")
