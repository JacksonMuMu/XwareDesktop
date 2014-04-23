# -*- coding: utf-8 -*-

import logging
from launcher import app

from PyQt5.QtWidgets import QMessageBox
from PyQt5.QtCore import Qt, QObject, pyqtSignal, pyqtSlot, QUrl, QVariant, QEvent
from PyQt5.QtGui import QKeyEvent, QDesktopServices

import collections

import constants, actions
from actions import FrontendActionsQueue

FrontendStatus = collections.namedtuple("FrontendStatus", ["xdjsLoaded", "logined", "online"])


# Together with xwarejs.js, exchange information with the browser
class FrontendPy(QObject):
    sigCreateTasks = pyqtSignal("QStringList")
    sigCreateTaskFromTorrentFile = pyqtSignal()
    sigCreateTaskFromTorrentFileDone = pyqtSignal()
    sigLogin = pyqtSignal(str, str)
    sigToggleFlashAvailability = pyqtSignal(bool)
    sigActivateDevice = pyqtSignal(str)
    sigNotifyPeerId = pyqtSignal(str)  # let xdjs knows peerid
    sigFrontendStatusChanged = pyqtSignal()  # caused by page heartbeat/changed status/refresh page

    queue = None
    _isPageMaskOn = None
    _isPageOnline = None  # property wraps them, in order to fire sigFrontendStatusChanged
    _isPageLogined = None
    _isXdjsLoaded = None

    def __init__(self, parent):
        super().__init__(parent)
        app.settings.applySettings.connect(self.tryLogin)
        self.queue = FrontendActionsQueue(self)
        app.sigMainWinLoaded.connect(self.connectUI)

    @pyqtSlot()
    def connectUI(self):
        app.mainWin.action_createTask.triggered.connect(self.queue.createTasksAction)

    @property
    def isPageMaskOn(self):
        return self._isPageMaskOn

    @isPageMaskOn.setter
    def isPageMaskOn(self, value):
        self._isPageMaskOn = value
        if self._isPageMaskOn is False:
            self.consumeAction("mask off")

    @property
    def isPageOnline(self):
        return self._isPageOnline

    @isPageOnline.setter
    def isPageOnline(self, value):
        if self._isPageOnline == value:
            return  # Heartbeat, don't need to continue if online status stays the same
        self._isPageOnline = value
        self.sigFrontendStatusChanged.emit()
        if self._isPageOnline:
            self.consumeAction("online")

    @property
    def isPageLogined(self):
        return self._isPageLogined

    @isPageLogined.setter
    def isPageLogined(self, value):
        self._isPageLogined = value
        self.sigFrontendStatusChanged.emit()
        if self._isPageLogined:
            self.consumeAction("logined")

    @property
    def isXdjsLoaded(self):
        return self._isXdjsLoaded

    @isXdjsLoaded.setter
    def isXdjsLoaded(self, value):
        self._isXdjsLoaded = value
        self.sigFrontendStatusChanged.emit()
        if self._isXdjsLoaded:
            self.consumeAction("xdjs loaded")

    @pyqtSlot()
    def tryLogin(self):
        if app.mainWin.page.urlMatch(constants.LOGIN_PAGE):
            autologin = app.settings.getbool("account", "autologin")
            if autologin:
                username = app.settings.get("account", "username")
                password = app.settings.get("account", "password")
                if username and password:
                    self.sigLogin.emit(username, password)

    def tryActivate(self, payload):
        if not app.mainWin.page.urlMatch(constants.V3_PAGE):
            return  # not v3 page

        if not payload["userid"]:
            return  # not logged in

        userid, status, code, peerid = app.etmpy.getActivationStatus()

        if userid == 0:
            # unbound
            if status == -1:
                QMessageBox.warning(None, "Xware Desktop 警告", "ETM未启用，无法激活。需要启动ETM后，刷新页面。",
                                    QMessageBox.Ok, QMessageBox.Ok)
                return

            elif status == 0 and code:
                self.sigActivateDevice.emit(code)  # to activate
                return

        else:
            if status == 0 and code:
                # re-activate
                self.sigActivateDevice.emit(code)
                return

            elif userid != int(payload["userid"]):
                QMessageBox.warning(None, "Xware Desktop 警告", "登录的迅雷账户不是绑定的迅雷账户。",
                                    QMessageBox.Ok, QMessageBox.Ok)
                return

            elif peerid not in payload["peerids"]:
                logging.warning("Network is slow, there're no peerids when xdjs loaded.")

            self.sigNotifyPeerId.emit(peerid)

    @pyqtSlot(QVariant)
    def xdjsLoaded(self, payload):
        logging.info("xdjs loaded")
        self.isXdjsLoaded = True
        self.tryLogin()
        self.tryActivate(payload)

    @pyqtSlot()
    def requestFocus(self):
        app.mainWin.restore()
        app.mainWin.frame.setFocus()

    @pyqtSlot(str)
    def systemOpen(self, url):
        url = app.mountsFaker.convertToNativePath(url)
        qurl = QUrl.fromLocalFile(url)
        QDesktopServices().openUrl(qurl)

    @pyqtSlot(str, str)
    def saveCredentials(self, username, password):
        app.settings.set("account", "username", username)
        app.settings.set("account", "password", password)
        app.settings.setbool("account", "autologin", True)
        app.settings.save()

    @pyqtSlot("QList<QVariant>")
    def log(self, items):
        print("xdjs: ", end = "")
        for item in items:
            print(item, end = " ")
        print("")

    @pyqtSlot(bool)
    def slotMaskOnOffChanged(self, maskon):
        self.isPageMaskOn = maskon

    @pyqtSlot(bool)
    def slotSetOnline(self, online):
        self.isPageOnline = online

    @pyqtSlot(bool)
    def slotSetLogined(self, logined):
        self.isPageLogined = logined

    @pyqtSlot()
    def consumeAction(self, reason):
        print("Try to consume, because {}.".format(reason))
        if not self.isPageOnline:
            print("Xdjs says device not online, no consuming")
            return

        if self.isPageMaskOn:
            print("Mask on, no consuming")
            return

        if not self.isXdjsLoaded:
            print("Xdjs not ready, no consuming")
            return

        if not self.isPageLogined:
            print("page not logined, no consuming")
            return

        try:
            action = self.queue.dequeueAction()
        except IndexError:
            print("Nothing to consume")
            # no actions
            return

        print("consuming action", action)
        if isinstance(action, actions.CreateTasksAction):
            taskUrls = list(map(lambda task: task.url, action.tasks))
            if action.tasks[0].kind == actions.CreateTask.NORMAL:
                self.sigCreateTasks.emit(taskUrls)
            else:
                app.mainWin.page.overrideFile = taskUrls[0]
                self.sigCreateTaskFromTorrentFile.emit()

    @pyqtSlot()
    def slotClickBtButton(self):
        keydownEvent = QKeyEvent(QEvent.KeyPress,  # type
                                 Qt.Key_Enter,     # key
                                 Qt.NoModifier)    # modifiers

        app.sendEvent(app.mainWin.webView, keydownEvent)
        self.sigCreateTaskFromTorrentFileDone.emit()

    def getFrontendStatus(self):
        return FrontendStatus(self.isXdjsLoaded, self.isPageLogined, self.isPageOnline)
