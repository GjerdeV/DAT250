from datetime import datetime, timedelta
from flask import render_template, request, redirect, url_for, make_response
from flask_sqlalchemy import SQLAlchemy
import string
import random
import pyotp
from flask_scrypt import generate_random_salt, generate_password_hash, check_password_hash

from app import app, db, cookie_maxAge, client_maxAge, NUMBER_OF_LOGIN_ATTEMPTS, BLOCK_LOGIN_TIME # Importerer Flask objektet app
from tools import send_mail, is_number, random_string_generator, contain_allowed_symbols, print_userdata, Norwegian_characters
from tools import valid_date, valid_email, valid_id, valid_name, valid_address, valid_number, valid_password, get_valid_cookie
from tools import generate_account_numbers, valid_account_number, generate_QR, generate_Captcha

from models import User, Cookies, Blacklist, Account, Transaction

# Denne er bare for POST forespørsler.
@app.route("/<data>", methods=['POST'])  # https://flask.palletsprojects.com/en/1.1.x/quickstart/
def post_data(data = None):
    print("post_handlers - 1")

    # Kontrollerer brukernavn og passord som er skrevet inn i login siden
    if data == "login_data":
        user_id = request.form.get("uname")
        
        if valid_id(user_id) == "":  # Sjekker om id'en vi mottok er i orden før vi prøver å søke gjennom databasen med den.
            client_listing = Blacklist.query.filter_by(ip=request.remote_addr).first()

            # Hvis login for denne clienten (ikke brukeren) har vært blokket, og brukeren ikke lenger er det
            if client_listing.blocked_login_until is not None and datetime.now() > datetime.strptime(client_listing.blocked_login_until, "%Y-%m-%d %H:%M:%S.%f"):
                client_listing.blocked_login_until = None
                db.session.commit()
                return redirect(url_for('login'), code=302)

            # Hvis clienten ikke er blokka
            if client_listing.blocked_login_until is None:

                user_object = User.query.filter_by(user_id=int(user_id)).first()

                if user_object is not None:
                    print_userdata(user_object)

                # Hvis brukeren er verifisert
                authenticator_code = request.form.get('auth_code')
                if user_object is not None and (request.form.get("uname") == str(user_object.user_id)) and str(pyotp.TOTP(user_object.secret_key).now()) == authenticator_code and check_password_hash(request.form.get("pswd"), user_object.hashed_password, user_object.salt) and user_object.verified:
                    # https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Set-Cookie
                    # Cookies names starting with __Secure- must be set with the secure flag from a secure page (HTTPS)
                    # A secure cookie is only sent to the server when a request is made with the https: scheme. 
                    # Max-Age=<number>, Number of seconds until the cookie expires.
                    # Forbids JavaScript from accessing the cookie. This mitigates attacks against cross-site scripting (XSS).

                    # Generer session cookie, den blir sendt til serveren av klienten ved hver request (innlasting av bilder, html, css). Cookie'en blir brukt til å identifisere brukeren
                    sessionId = random_string_generator(128)
                    expiration_date = datetime.now() + timedelta(seconds=cookie_maxAge)

                    cookie = Cookies(user_id=user_object.user_id, ip=request.remote_addr, session_cookie=sessionId, valid_to=str(expiration_date))
                    db.session.add(cookie)
                    db.session.commit()

                    resp = make_response(redirect(url_for('startpage'), code=302))

                    if "https://" in request.host_url:
                        resp.headers.set('Set-Cookie', "__Secure-sessionId=" + sessionId + "; Max-Age=" + str(cookie_maxAge + client_maxAge) + "; SameSite=Strict; Secure; HttpOnly")
                    else:
                        resp.headers.set('Set-Cookie', "sessionId=" + sessionId + "; Max-Age=" + str(cookie_maxAge + client_maxAge) + "; SameSite=Strict; HttpOnly")
                    return resp

                # Hvis brukeren ikke er verifisert, og oppgir riktig midlertidig passord
                elif user_object is not None and (request.form.get("uname") == str(user_object.user_id)) and check_password_hash(request.form.get("pswd"), user_object.hashed_password, user_object.salt) and not user_object.verified:
                    return redirect(url_for('verification', code=user_object.verification_code), code=302)
                
                # Et feilet innloggingsforsøk
                else:
                    client_listing.wrong_password_count += 1
                    print(f"client_listing.wrong_password_count = {client_listing.wrong_password_count}")

                    if client_listing.wrong_password_count >= NUMBER_OF_LOGIN_ATTEMPTS:
                        client_listing.blocked_login_until = str(datetime.now() + timedelta(seconds=BLOCK_LOGIN_TIME))
                        client_listing.wrong_password_count = 0

                    db.session.commit()

            elif client_listing.blocked_login_until is not None and datetime.now() <= datetime.strptime(client_listing.blocked_login_until, "%Y-%m-%d %H:%M:%S.%f"):
                return redirect(url_for('login'), code=302)
            
        # Hvis ugyldig bruker, send brukeren "tilbake" til login siden, men vis en feilmelding (se html koden til login.html for hva og hvordan feilkoden vises)
        return redirect(url_for('login', error="True"), code=302)

    elif data == "verification":
        # Sjekk om den matcher med en bruker i databasen før vi setter passord og authenticator kode for brukeren.
        # Dette må gjøres for å identifisere hvem som prøver å verifisere seg.
        if 'Referer' in request.headers:
            verification_code = request.headers.get('Referer').split('verification?code=')[1].replace('&error=True', '')  # request.headers.get('Referer')

            if contain_allowed_symbols(s=verification_code, whitelist=string.ascii_letters + string.digits):  # Kontrollerer om koden inneholder gyldige symboler før vi prøver å søke gjennom databasen med den.
                user_object = User.query.filter_by(verification_code=verification_code).first()

                pswd = request.form.get('pswd')  # request.form['pswd'] brukes denne så krasjer koden om noen med vilje ikke oppgir pswd
                conf_pswd = request.form.get('conf_pswd')
                secret_key = user_object.secret_key
                authenticator_code = request.form.get('auth_code')
                ############################
                captcha_input = request.form.get('captcha_input')
                ############################
                totp = pyotp.TOTP(secret_key).now()
                salt = generate_random_salt()
                password_hash = generate_password_hash(pswd, salt)
                print(totp)
                print(authenticator_code)   
                print_userdata(user_object)
                # Hvis det er en konto med denne verifiseringskoden, passordene er like, gyldige, og ikke lik det midlertidige passorde
                if user_object is not None and pswd == conf_pswd and valid_number(authenticator_code, 6, 6) and authenticator_code == totp and valid_password(pswd) and not check_password_hash(request.form.get("pswd"), user_object.hashed_password, user_object.salt):
                    user_object.verification_code = None            # Deaktiver verifiseringslinken til brukeren
                    user_object.hashed_password = password_hash
                    user_object.salt = salt
                    
                    user_object.verified = 1                        # Marker som verifisert
                    # Oppretter brukskonto og sparekonto for brukeren
                    account_numbers = generate_account_numbers(amount=2)
                    regular_account = Account(user_id=str(user_object.user_id), account_number=account_numbers[0], account_name="Main", balance=8421)
                    savings_account = Account(user_id=str(user_object.user_id), account_number=account_numbers[1], account_name="Savings", balance=22458)

                    # Legg kontoene til i databasen, og lagre alle database endringer
                    db.session.add(regular_account)
                    db.session.add(savings_account)
                    db.session.commit() # Lagre
                    
                    return redirect(url_for('login'), code=302)
                else:
                    return redirect(url_for('verification', code=verification_code, error="True"), code=302)

    elif data == "reset_request":
        if valid_id(request.form.get("uname")) == "":
            user_object = User.query.filter_by(user_id=int(request.form.get("uname"))).first()
            if user_object is not None and user_object.verified:
                code = random_string_generator(128)
                user_object.password_reset_code = code

                reset_link = request.host_url + 'reset?code=' + code  # Lager linken brukeren skal få i mailen.
                html_template = render_template('/mails/password_reset.html', fname=user_object.fname, mname=user_object.mname, lname=user_object.lname, link=reset_link)
                
                # Denne må være med for å kunne sende bekreftelses mailen, men kan kommenteres vekk under testing slik at en slipper å få så mange mailer.
                send_mail(recipients=[user_object.email], subject="Password reset", body="TEST_BODY", html=html_template)

                db.session.commit()

    elif data == "reset_password":
        if 'Referer' in request.headers:
            password_reset_code = request.headers.get('Referer').split('reset?code=')[1].replace('&error=True', '')

            pswd = request.form.get('pswd')  # request.form['pswd'] brukes denne så krasjer koden om noen med vilje ikke oppgir pswd
            conf_pswd = request.form.get('conf_pswd')

            if contain_allowed_symbols(s=password_reset_code, whitelist=string.ascii_letters + string.digits):
                user_object = User.query.filter_by(password_reset_code=password_reset_code).first()

                if user_object is not None and valid_password(pswd) and pswd == conf_pswd and not check_password_hash(request.form.get("pswd"), user_object.hashed_password, user_object.salt):
                    salt = generate_random_salt()
                    password_hash = generate_password_hash(pswd, salt)
                    
                    user_object.hashed_password = password_hash
                    user_object.salt = salt
                    user_object.password_reset_code = None

                    db.session.commit()
                else:
                    return redirect(url_for('reset', code=password_reset_code, error="True"), code=302)

    # Verifiser data fra bruker, lag bruker, sett info inn i databasen, send bekreftelsesmail.
    elif data == "registration_data":
        # Her legges eventuelle feilmeldinger angående dataen fra registreringssiden.
        feedback = {'fname': '', 'mname': '', 'lname': '', 'email': '', 'id': '', 'phone_num': '', 'dob': '', 'city': '', 'postcode': '', 'address': ''}

        # Er fødselsdatoen gyldig?
        if not valid_date(request.form.get("dob")):
            feedback["dob"] = "invalid"

        # Er epost gyldig?
        feedback["email"] = valid_email(request.form.get("email"))

        # Matcher bruker id og epost?
        email = request.form.get("email").split('@')
        if feedback["email"] == '' and email[0] != request.form.get("id"): # Hvis epost er gyldig, Sjekk om id ikke stemmer overens med epost
            feedback["email"] = "mismatch"
            feedback["id"] = "mismatch"

        # Er bruker id'en gyldig?
        feedback["id"] = valid_id(request.form.get("id"))

        # Er fornavn, mellomnavn og etternavn gyldig?
        feedback["fname"] = valid_name(names=request.form.get("fname"), whitelist=string.ascii_letters + '-' + Norwegian_characters)  # Godtar bindestrek i navn
        if request.form.get("mname") != "":  # Trenger ikke å ha mellomnavn, men hvis det har blitt skrevet inn, kontroller det.
            feedback["mname"] = valid_name(names=request.form.get("mname"))
        feedback["lname"] = valid_name(names=request.form.get("lname"))

        # Er by navn gyldig?
        feedback["city"] = valid_name(request.form.get("city"))

        # Er telefonnummer gyldig?
        if not valid_number(number=request.form.get("phone_num"), min_length=8, max_length=8):
            feedback["phone_num"] = "NaN"

        # Er post kode gyldig?
        if not valid_number(number=request.form.get("postcode"), min_length=4, max_length=4):
            feedback["postcode"] = "NaN"

        # Er addressen gyldig?
        feedback["address"] = valid_address(request.form.get("address"))

        # Har det oppstått noen feil?
        error = False
        for element in feedback:
            if feedback[element] != '':  # Hvis innholde ikke er tomt, så har det oppstått en feil
                error = True

        # Hvis det har oppstått noen feil, send brukeren "tilbake" til registreringssiden med feilmeldingene
        if error:
            return redirect(url_for('registration', fname=feedback["fname"], mname=feedback["mname"], lname=feedback["lname"], 
                                                    email=feedback["email"], id=feedback["id"], phone_num=feedback["phone_num"], 
                                                    dob=feedback["dob"], city=feedback["city"], postcode=feedback["postcode"], 
                                                    address=feedback["address"]), code=302)

        # Hvis brukeren allerede finnes, send klienten tilbake til login siden som om brukeren faktisk klarte å registrere seg
        elif User.query.filter_by(user_id=int(request.form.get("id"))).first() is not None:
            return redirect(url_for('login', v_mail="True"), code=302)
        # Ellers, lag en tilfeldig link som brukeren bruker til å verifisere seg selv. Denne linken mottas på epost.
        else:
            code = random_string_generator(128)             # Generate a random string of 128 symbols, this is the verification code
            temp_password = random_string_generator(32)     # Generate a random string of 20 symbols, considering removing this
            validation_link = request.host_url + 'verification?code=' + code  # Lager linken brukeren skal få i mailen.
            html_template = render_template('/mails/account_verification.html', fname=request.form.get("fname"), mname=request.form.get("mname"), 
                                                                                lname=request.form.get("lname"), link=validation_link,
                                                                                password=temp_password, uid=request.form.get("id"))
            
            # Denne må være med for å kunne sende bekreftelses mailen, men kan kommenteres vekk under testing slik at en slipper å få så mange mailer.
            send_mail(recipients=[request.form.get("email")], subject="Account verification", body="", html=html_template)

            salt = generate_random_salt()
            password_hash = generate_password_hash(temp_password, salt)
            secret_key,_ = generate_QR(request.form.get("fname"), request.form.get("id"))

            user_object = User( user_id=int(request.form.get("id")), 
                                email=request.form.get("email"), 
                                fname=request.form.get("fname"), 
                                mname=request.form.get("mname"),
                                lname=request.form.get("lname"), 
                                phone_num=int(request.form.get("phone_num")), 
                                dob=request.form.get("dob"), 
                                city=request.form.get("city"), 
                                postcode=int(request.form.get("postcode")), 
                                address=request.form.get("address"), 
                                hashed_password=password_hash,
                                salt=salt,
                                verification_code=code,
                                verified=0,
                                secret_key=secret_key,
                                failed_logins=0)

            db.session.add(user_object)
            db.session.commit()
            print_userdata(user_object)
            return redirect(url_for('login', v_mail="True"), code=302)

    # Verifiser data fra bruker osv
    elif data == "edit_data": 

        # Her legges eventuelle feilmeldinger angående dataen fra registreringssiden.
        feedback = {'fname_error': '', 'mname_error': '', 'lname_error': '', 'phone_num_error': '',
                     'dob_error': '', 'city_error': '', 'postcode_error': '', 'address_error': '', 'pswd_error': '', 'new_pswd_error': ''}

        # Er fødselsdatoen gyldig?
        if not valid_date(request.form.get("dob")):
            feedback["dob_error"] = "invalid"

        # Er fornavn, mellomnavn og etternavn gyldig?
        feedback["fname_error"] = valid_name(names=request.form.get("fname"), whitelist=string.ascii_letters + '-')  # Godtar bindestrek i navn
        if request.form.get("mname") != "":  # Trenger ikke å ha mellomnavn, men hvis det har blitt skrevet inn, kontroller det.
            feedback["mname_error"] = valid_name(names=request.form.get("mname"))
        feedback["lname_error"] = valid_name(names=request.form.get("lname"))

        # Er by navn gyldig?
        feedback["city_error"] = valid_name(request.form.get("city"))

        # Er telefonnummer gyldig?
        if not valid_number(number=request.form.get("phone_num"), min_length=8, max_length=8):
            feedback["phone_num_error"] = "NaN"

        # Er post kode gyldig?
        if not valid_number(number=request.form.get("postcode"), min_length=4, max_length=4):
            feedback["postcode_error"] = "NaN"

        # Er addressen gyldig?
        feedback["address_error"] = valid_address(request.form.get("address"))

        # Har det oppstått noen feil?
        error = False
        for element in feedback:
            if feedback[element] != '':  # Hvis innholde ikke er tomt, så har det oppstått en feil
                error = True

        # Hvis det har oppstått noen feil, send brukeren "tilbake" til redigeringssiden med feilmeldingene
        if error:
            return redirect(url_for('edit', fname=feedback["fname_error"], mname=feedback["mname_error"], lname=feedback["lname_error"], 
                                                    phone_num=feedback["phone_num_error"], dob=feedback["dob_error"], 
                                                    city=feedback["city_error"], postcode=feedback["postcode_error"], 
                                                    address=feedback["address_error"]), code=302)

        session_cookie = get_valid_cookie()  # Henter gyldig cookie fra headeren hvis det er en

        if session_cookie is not None:  # Om vi fikk en gyldig header
            cookie = Cookies.query.filter_by(session_cookie=session_cookie).first()
            user = User.query.filter_by(user_id=cookie.user_id).first()

            if valid_password(request.form.get("pswd")) and check_password_hash(request.form.get("pswd"), user.hashed_password, user.salt):
                user.fname = request.form.get("fname")
                user.mname = request.form.get("mname")
                user.lname = request.form.get("lname")
                user.phone_num = request.form.get("phone_num")
                user.dob = request.form.get("dob")
                user.city = request.form.get("city")
                user.address = request.form.get("address")
                user.postcode = request.form.get("postcode")

                new_pswd = request.form.get("new_pswd")
                new_pswd2 = request.form.get("new_pswd2")
                if new_pswd == new_pswd2 != "" and valid_password(new_pswd):

                    salt = generate_random_salt()
                    password_hash = generate_password_hash(new_pswd, salt)

                    user.salt = salt
                    user.hashed_password = password_hash

                elif new_pswd == new_pswd2 != "" and not valid_password(new_pswd):
                    feedback["new_pswd_error"] = "invalid"
                    return redirect(url_for('edit', new_pswd=feedback["new_pswd_error"], code=302))

                elif new_pswd != new_pswd2:
                    feedback["new_pswd_error"] = "unmatched"
                    return redirect(url_for('edit', new_pswd=feedback["new_pswd_error"], code=302))
            else:
                feedback["pswd_error"] = "incorrect"
                return redirect(url_for('edit', pswd=feedback["pswd_error"], code=302))

            db.session.commit()  # Etter endringer er gjort, lagre

            return redirect(url_for('din_side', code=302))

    #Betalingsfunksjon
    elif data == "payment":
        #eventuelle feilmeldinger som kan komme under betalingsfunksjonen
        feedback= {'account_num_error':'', 'date_error':'', 'account_balance_error': '', 'amount_error': ''}
         
        from_acc = request.form.get('account_num')
        to_acc = request.form.get('account_num_to')
        msg = request.form.get('kidnr')                # Stor risk for SQL Injection, hvilke symboler skal vi tillate, eventuelt, skal vi søke etter SQL kommandoer i teksten?
        
        # Sjekk om bruker kontoene er ulike og har gyldig format, i tillegg sjekk om belop er et tall og at det er større enn 0
        if from_acc != to_acc and valid_account_number(from_acc) and valid_account_number(to_acc) and is_number(request.form.get('belop')) and int(request.form.get('belop')) > 0:
            amount = int(request.form.get('belop'))            

            #Vertifiser bruker
            session_cookie = get_valid_cookie()  # Henter gyldig cookie fra headeren hvis det er en
            if session_cookie is not None:  # Om vi fikk en gyldig header
                cookie = Cookies.query.filter_by(session_cookie=session_cookie).first()
                user = User.query.filter_by(user_id=cookie.user_id).first()

                # Sjekk om brukeren prøver å sende penger fra en av sine kontoer
                account_from = Account.query.filter_by(account_number=from_acc).first()
                if account_from is not None and account_from.user_id == user.user_id:
                    
                    #Sjekk at belopet er gyldig (ikke mer enn det de har på kontoen)
                    if account_from.balance >= amount:
                    
                        # sjekker om du prøver å sende til en eksisterende konto
                        account_to = Account.query.filter_by(account_number=to_acc).first()
                        if account_to is not None:
                            account_from.balance -= amount
                            account_to.balance += amount

                            # Loggfør transaksjon
                            transaction = Transaction(transfer_time=str(datetime.now()), from_acc=from_acc, to_acc=to_acc, message=msg, amount=amount)
                            db.session.add(transaction)

                            db.session.commit()

    # Hvis vi får en ugyldig POST forespørsel eller if'ene ikke sender brukeren til en spesifik side, send brukeren til fremsiden
    return redirect(url_for('index'), code=302)

# https://tedboy.github.io/flask/generated/generated/werkzeug.ImmutableMultiDict.html
# https://stackoverflow.com/questions/10434599/get-the-data-received-in-a-flask-request
# request.form